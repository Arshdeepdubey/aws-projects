"""Multimodal RAG infrastructure.

OpenSearch Serverless needs three policies before a collection works at all —
encryption, network and data access — and the data access policy must name the
IAM principals (here: the Lambda roles) explicitly. Getting that wrong is the
usual cause of a 403 from an otherwise healthy collection.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as sources,
    aws_logs as logs,
    aws_opensearchserverless as aoss,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_sqs as sqs,
)
from constructs import Construct


class MultimodalRagStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        environment_name: str,
        bedrock_region: str,
        index_name: str,
        models: dict[str, str],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        name = f"{project}-{environment_name}"
        collection_name = name[:32]  # AOSS collection names are limited to 32 chars

        # ------------------------------------------------------------ storage
        self.documents = s3.Bucket(
            self,
            "DocumentsBucket",
            bucket_name=f"{name}-documents-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
        )

        # Extracted page images, Textract output, captions — derived, regenerable.
        self.derived = s3.Bucket(
            self,
            "DerivedBucket",
            bucket_name=f"{name}-derived-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Ingestion bookkeeping: what has been indexed, how many chunks, failures.
        self.documents_table = dynamodb.Table(
            self,
            "DocumentsTable",
            table_name=f"{name}-documents",
            partition_key=dynamodb.Attribute(name="documentId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ----------------------------------------------------- vector store
        encryption_policy = aoss.CfnSecurityPolicy(
            self,
            "EncryptionPolicy",
            name=f"{collection_name}-enc",
            type="encryption",
            policy=json.dumps(
                {
                    "Rules": [
                        {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]}
                    ],
                    "AWSOwnedKey": True,
                }
            ),
        )

        network_policy = aoss.CfnSecurityPolicy(
            self,
            "NetworkPolicy",
            name=f"{collection_name}-net",
            type="network",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]},
                            {"ResourceType": "dashboard", "Resource": [f"collection/{collection_name}"]},
                        ],
                        # Public endpoint with IAM auth. For a private deployment, replace this
                        # with SourceVPCEs and put the Lambdas in the VPC.
                        "AllowFromPublic": True,
                    }
                ]
            ),
        )

        self.collection = aoss.CfnCollection(
            self,
            "VectorCollection",
            name=collection_name,
            type="VECTORSEARCH",
            description=f"Multimodal RAG vectors for {name}",
        )
        self.collection.add_dependency(encryption_policy)
        self.collection.add_dependency(network_policy)

        # ---------------------------------------------------------- queueing
        self.ingestion_dlq = sqs.Queue(
            self,
            "IngestionDlq",
            queue_name=f"{name}-ingestion-dlq",
            retention_period=Duration.days(14),
        )

        self.ingestion_queue = sqs.Queue(
            self,
            "IngestionQueue",
            queue_name=f"{name}-ingestion",
            visibility_timeout=Duration.minutes(16),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=self.ingestion_dlq),
        )

        self.textract_topic = sns.Topic(self, "TextractTopic", topic_name=f"{name}-textract")

        # ---------------------------------------------------------- functions
        common_env = {
            "COLLECTION_ENDPOINT": self.collection.attr_collection_endpoint,
            "INDEX_NAME": index_name,
            "BEDROCK_REGION": bedrock_region,
            "TEXT_EMBEDDING_MODEL": models["text_embedding"],
            "MULTIMODAL_EMBEDDING_MODEL": models["multimodal_embedding"],
            "GENERATION_MODEL": models["generation"],
            "VISION_MODEL": models["vision"],
            "DOCUMENTS_BUCKET": self.documents.bucket_name,
            "DERIVED_BUCKET": self.derived.bucket_name,
            "DOCUMENTS_TABLE": self.documents_table.table_name,
            "LOG_LEVEL": "INFO",
        }

        self.ingestion_fn = lambda_.Function(
            self,
            "IngestionFunction",
            function_name=f"{name}-ingestion",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src", exclude=["query/*", "__pycache__", "**/__pycache__"]),
            timeout=Duration.minutes(15),
            memory_size=2048,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                **common_env,
                "TEXTRACT_TOPIC_ARN": self.textract_topic.topic_arn,
                "CHUNK_SIZE": "1200",
                "CHUNK_OVERLAP": "180",
            },
        )
        # The asset root is src/, so the handler lives at ingestion/handler.py.
        self.ingestion_fn.add_environment("PYTHONPATH", "/var/task")
        cfn_ingestion = self.ingestion_fn.node.default_child
        cfn_ingestion.add_property_override("Handler", "ingestion/handler.lambda_handler")

        self.query_fn = lambda_.Function(
            self,
            "QueryFunction",
            function_name=f"{name}-query",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src", exclude=["ingestion/*", "__pycache__", "**/__pycache__"]),
            timeout=Duration.seconds(120),
            memory_size=1024,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={**common_env, "DEFAULT_TOP_K": "6", "MAX_CONTEXT_CHARS": "24000"},
        )
        cfn_query = self.query_fn.node.default_child
        cfn_query.add_property_override("Handler", "query/handler.lambda_handler")

        # ------------------------------------------------------------- wiring
        self.documents.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.SqsDestination(self.ingestion_queue),
            s3.NotificationKeyFilter(prefix="documents/"),
        )
        self.ingestion_fn.add_event_source(
            sources.SqsEventSource(self.ingestion_queue, batch_size=1, report_batch_item_failures=True)
        )
        self.textract_topic.add_subscription(subs.LambdaSubscription(self.ingestion_fn))

        self.documents.grant_read_write(self.ingestion_fn)
        self.derived.grant_read_write(self.ingestion_fn)
        self.documents_table.grant_read_write_data(self.ingestion_fn)
        self.documents.grant_read(self.query_fn)
        self.derived.grant_read(self.query_fn)

        textract_service_role = iam.Role(
            self,
            "TextractPublishRole",
            assumed_by=iam.ServicePrincipal("textract.amazonaws.com"),
            description="Lets Textract publish job completion to SNS",
        )
        self.textract_topic.grant_publish(textract_service_role)
        self.ingestion_fn.add_environment("TEXTRACT_ROLE_ARN", textract_service_role.role_arn)

        self.ingestion_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "textract:StartDocumentTextDetection",
                    "textract:GetDocumentTextDetection",
                    "textract:DetectDocumentText",
                    "textract:AnalyzeDocument",
                ],
                resources=["*"],
            )
        )
        self.ingestion_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["iam:PassRole"], resources=[textract_service_role.role_arn])
        )

        bedrock_policy = iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                f"arn:aws:bedrock:{bedrock_region}::foundation-model/*",
                f"arn:aws:bedrock:{bedrock_region}:{self.account}:inference-profile/*",
            ],
        )
        self.ingestion_fn.add_to_role_policy(bedrock_policy)
        self.query_fn.add_to_role_policy(bedrock_policy)

        aoss_api = iam.PolicyStatement(
            actions=["aoss:APIAccessAll"],
            resources=[f"arn:aws:aoss:{self.region}:{self.account}:collection/{self.collection.attr_id}"],
        )
        self.ingestion_fn.add_to_role_policy(aoss_api)
        self.query_fn.add_to_role_policy(aoss_api)

        # Data access policy: without these principals listed, every request 403s.
        aoss.CfnAccessPolicy(
            self,
            "DataAccessPolicy",
            name=f"{collection_name}-data",
            type="data",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {
                                "ResourceType": "index",
                                "Resource": [f"index/{collection_name}/*"],
                                "Permission": [
                                    "aoss:CreateIndex",
                                    "aoss:DeleteIndex",
                                    "aoss:UpdateIndex",
                                    "aoss:DescribeIndex",
                                    "aoss:ReadDocument",
                                    "aoss:WriteDocument",
                                ],
                            },
                            {
                                "ResourceType": "collection",
                                "Resource": [f"collection/{collection_name}"],
                                "Permission": [
                                    "aoss:CreateCollectionItems",
                                    "aoss:DescribeCollectionItems",
                                    "aoss:UpdateCollectionItems",
                                ],
                            },
                        ],
                        "Principal": [
                            self.ingestion_fn.role.role_arn,
                            self.query_fn.role.role_arn,
                            # So a human can create the index and inspect documents.
                            f"arn:aws:iam::{self.account}:root",
                        ],
                    }
                ]
            ),
        )

        # ------------------------------------------------------------- api
        http_api = apigw.HttpApi(self, "RagApi", api_name=f"{name}-api")
        http_api.add_routes(
            path="/ask",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("AskIntegration", self.query_fn),
        )

        # ----------------------------------------------------------- outputs
        cdk.CfnOutput(self, "DocumentsBucket", value=self.documents.bucket_name)
        cdk.CfnOutput(self, "DerivedBucket", value=self.derived.bucket_name)
        cdk.CfnOutput(self, "CollectionEndpoint", value=self.collection.attr_collection_endpoint)
        cdk.CfnOutput(self, "CollectionId", value=self.collection.attr_id)
        cdk.CfnOutput(self, "IndexName", value=index_name)
        cdk.CfnOutput(self, "ApiUrl", value=http_api.api_endpoint)
        cdk.CfnOutput(self, "IngestionDlqUrl", value=self.ingestion_dlq.queue_url)
