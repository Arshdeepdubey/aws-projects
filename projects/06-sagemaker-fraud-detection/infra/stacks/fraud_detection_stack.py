"""Infrastructure for the fraud detection solution.

Deliberately split: everything here is long-lived (buckets, roles, registry, the
inference API). The model itself is produced by a SageMaker Pipeline run, because
training belongs in a pipeline you can re-run, not in a CloudFormation deployment.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_cloudwatch as cw,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_sagemaker as sagemaker,
    aws_ssm as ssm,
)
from constructs import Construct


class FraudDetectionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        environment_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        name = f"{project}-{environment_name}"
        self.endpoint_name = name

        # ------------------------------------------------------------ storage
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"{name}-data-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-processing-artifacts",
                    prefix="processing/",
                    expiration=Duration.days(30),
                )
            ],
        )

        self.artifacts_bucket = s3.Bucket(
            self,
            "ArtifactsBucket",
            bucket_name=f"{name}-artifacts-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ------------------------------------------------- sagemaker execution
        self.sagemaker_role = iam.Role(
            self,
            "SageMakerExecutionRole",
            role_name=f"{name}-sagemaker",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            description="Used by processing, training and endpoint containers",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess")
            ],
        )
        self.data_bucket.grant_read_write(self.sagemaker_role)
        self.artifacts_bucket.grant_read_write(self.sagemaker_role)

        # --------------------------------------------------------- registry
        self.model_package_group = sagemaker.CfnModelPackageGroup(
            self,
            "ModelPackageGroup",
            model_package_group_name=f"{name}-models",
            model_package_group_description="Fraud detection XGBoost models",
        )

        # Threshold chosen during evaluation; the inference Lambda reads it at runtime
        # so retraining can move it without a code deploy.
        self.threshold_parameter = ssm.StringParameter(
            self,
            "DecisionThreshold",
            parameter_name=f"/{project}/{environment_name}/decision-threshold",
            string_value="0.5",
            description="Probability above which a transaction is flagged as fraud",
        )

        # --------------------------------------------------------- inference
        self.inference_fn = lambda_.Function(
            self,
            "InferenceFunction",
            function_name=f"{name}-inference",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src/inference"),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "ENDPOINT_NAME": self.endpoint_name,
                "THRESHOLD_PARAMETER": self.threshold_parameter.parameter_name,
                "LOG_LEVEL": "INFO",
            },
        )

        self.inference_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sagemaker:InvokeEndpoint"],
                resources=[
                    f"arn:aws:sagemaker:{self.region}:{self.account}:endpoint/{self.endpoint_name}"
                ],
            )
        )
        self.threshold_parameter.grant_read(self.inference_fn)

        http_api = apigw.HttpApi(
            self,
            "InferenceApi",
            api_name=f"{name}-api",
            description="Fraud scoring API",
        )
        http_api.add_routes(
            path="/predict",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("PredictIntegration", self.inference_fn),
        )

        # ------------------------------------------------------------ alarms
        cw.Alarm(
            self,
            "InferenceErrors",
            alarm_name=f"{name}-inference-errors",
            metric=self.inference_fn.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )

        cw.Alarm(
            self,
            "EndpointLatency",
            alarm_name=f"{name}-endpoint-latency",
            metric=cw.Metric(
                namespace="AWS/SageMaker",
                metric_name="ModelLatency",
                dimensions_map={
                    "EndpointName": self.endpoint_name,
                    "VariantName": "AllTraffic",
                },
                statistic="p99",
                period=Duration.minutes(5),
            ),
            threshold=1_000_000,  # microseconds = 1s
            evaluation_periods=3,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )

        # ----------------------------------------------------------- outputs
        cdk.CfnOutput(self, "DataBucket", value=self.data_bucket.bucket_name)
        cdk.CfnOutput(self, "ArtifactsBucket", value=self.artifacts_bucket.bucket_name)
        cdk.CfnOutput(self, "SageMakerRoleArn", value=self.sagemaker_role.role_arn)
        cdk.CfnOutput(self, "ModelPackageGroupName", value=f"{name}-models")
        cdk.CfnOutput(self, "EndpointName", value=self.endpoint_name)
        cdk.CfnOutput(self, "ApiUrl", value=http_api.api_endpoint)
        cdk.CfnOutput(self, "ThresholdParameter", value=self.threshold_parameter.parameter_name)
