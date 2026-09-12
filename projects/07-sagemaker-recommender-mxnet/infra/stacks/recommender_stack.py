"""Infrastructure for the MXNet recommender: storage, training role, serving path."""

from __future__ import annotations

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
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class RecommenderStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        environment_name: str,
        enable_endpoint: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        name = f"{project}-{environment_name}"
        endpoint_name = name

        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"{name}-data-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # One item per user holding the precomputed top-N. Reads are a single
        # GetItem; writes happen once per batch run.
        self.table = dynamodb.Table(
            self,
            "RecommendationsTable",
            table_name=f"{name}-recs",
            partition_key=dynamodb.Attribute(name="userId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="expiresAt",
        )

        self.sagemaker_role = iam.Role(
            self,
            "SageMakerRole",
            role_name=f"{name}-sagemaker",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess")
            ],
        )
        self.data_bucket.grant_read_write(self.sagemaker_role)

        self.api_fn = lambda_.Function(
            self,
            "RecommendationsApi",
            function_name=f"{name}-api",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src/serving"),
            timeout=Duration.seconds(15),
            memory_size=512,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "RECS_TABLE": self.table.table_name,
                "ENDPOINT_NAME": endpoint_name if enable_endpoint else "",
                "DEFAULT_LIMIT": "10",
                "LOG_LEVEL": "INFO",
            },
        )
        self.table.grant_read_data(self.api_fn)

        if enable_endpoint:
            self.api_fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["sagemaker:InvokeEndpoint"],
                    resources=[
                        f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/{endpoint_name}"
                    ],
                )
            )

        http_api = apigw.HttpApi(self, "Api", api_name=f"{name}-api")
        http_api.add_routes(
            path="/users/{userId}/recommendations",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration("RecsIntegration", self.api_fn),
        )

        cdk.CfnOutput(self, "DataBucket", value=self.data_bucket.bucket_name)
        cdk.CfnOutput(self, "RecommendationsTable", value=self.table.table_name)
        cdk.CfnOutput(self, "SageMakerRoleArn", value=self.sagemaker_role.role_arn)
        cdk.CfnOutput(self, "ApiUrl", value=http_api.api_endpoint)
        cdk.CfnOutput(self, "EndpointName", value=endpoint_name if enable_endpoint else "(disabled)")
