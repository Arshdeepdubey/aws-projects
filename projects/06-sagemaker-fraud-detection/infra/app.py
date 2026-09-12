#!/usr/bin/env python3
"""CDK entrypoint for the fraud detection solution."""

import os

import aws_cdk as cdk

from stacks.fraud_detection_stack import FraudDetectionStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "ap-south-1"),
)

stack = FraudDetectionStack(
    app,
    "FraudDetectionStack",
    env=env,
    project=app.node.try_get_context("project") or "fraud-detection",
    environment_name=app.node.try_get_context("environment") or "dev",
)

cdk.Tags.of(stack).add("Project", "fraud-detection")
cdk.Tags.of(stack).add("ManagedBy", "cdk")
cdk.Tags.of(stack).add("Repo", "aws-lambda-functions")

app.synth()
