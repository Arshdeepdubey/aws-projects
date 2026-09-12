#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.image_classifier_stack import ImageClassifierStack

app = cdk.App()

stack = ImageClassifierStack(
    app,
    "ImageClassifierStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ap-south-1"),
    ),
    project=app.node.try_get_context("project") or "image-classifier",
    environment_name=app.node.try_get_context("environment") or "dev",
    confidence_threshold=float(app.node.try_get_context("confidence_threshold") or 0.75),
)

cdk.Tags.of(stack).add("Project", "image-classifier")
cdk.Tags.of(stack).add("ManagedBy", "cdk")
cdk.Tags.of(stack).add("Repo", "aws-lambda-functions")

app.synth()
