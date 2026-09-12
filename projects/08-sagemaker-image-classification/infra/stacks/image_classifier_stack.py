"""Infrastructure for the image classification system."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudwatch as cw,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as sources,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class ImageClassifierStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        environment_name: str,
        confidence_threshold: float = 0.75,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        name = f"{project}-{environment_name}"
        endpoint_name = name

        self.images_bucket = s3.Bucket(
            self,
            "ImagesBucket",
            bucket_name=f"{name}-images-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-incoming",
                    prefix="incoming/",
                    expiration=Duration.days(90),
                )
            ],
        )

        self.predictions = dynamodb.Table(
            self,
            "PredictionsTable",
            table_name=f"{name}-predictions",
            partition_key=dynamodb.Attribute(name="imageKey", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.predictions.add_global_secondary_index(
            index_name="label-classifiedAt-index",
            partition_key=dynamodb.Attribute(name="label", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="classifiedAt", type=dynamodb.AttributeType.STRING),
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
        self.images_bucket.grant_read_write(self.sagemaker_role)

        self.classifier_fn = lambda_.Function(
            self,
            "ClassifierFunction",
            function_name=f"{name}-classifier",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src/inference"),
            timeout=Duration.seconds(60),
            memory_size=1024,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "ENDPOINT_NAME": endpoint_name,
                "PREDICTIONS_TABLE": self.predictions.table_name,
                "IMAGES_BUCKET": self.images_bucket.bucket_name,
                "CONFIDENCE_THRESHOLD": str(confidence_threshold),
                "REVIEW_PREFIX": "review/",
                "LOG_LEVEL": "INFO",
            },
        )

        self.images_bucket.grant_read_write(self.classifier_fn)
        self.predictions.grant_write_data(self.classifier_fn)
        self.classifier_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sagemaker:InvokeEndpoint"],
                resources=[
                    f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/{endpoint_name}"
                ],
            )
        )

        # Only the incoming/ prefix triggers classification, so training data
        # and review copies do not re-enter the pipeline.
        self.classifier_fn.add_event_source(
            sources.S3EventSource(
                self.images_bucket,
                events=[s3.EventType.OBJECT_CREATED],
                filters=[s3.NotificationKeyFilter(prefix="incoming/")],
            )
        )

        cw.Alarm(
            self,
            "ClassifierErrors",
            alarm_name=f"{name}-classifier-errors",
            metric=self.classifier_fn.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )

        cdk.CfnOutput(self, "ImagesBucket", value=self.images_bucket.bucket_name)
        cdk.CfnOutput(self, "PredictionsTable", value=self.predictions.table_name)
        cdk.CfnOutput(self, "SageMakerRoleArn", value=self.sagemaker_role.role_arn)
        cdk.CfnOutput(self, "EndpointName", value=endpoint_name)
