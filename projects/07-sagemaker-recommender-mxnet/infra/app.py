#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.recommender_stack import RecommenderStack

app = cdk.App()

stack = RecommenderStack(
    app,
    "RecommenderStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ap-south-1"),
    ),
    project=app.node.try_get_context("project") or "recommender",
    environment_name=app.node.try_get_context("environment") or "dev",
    enable_endpoint=bool(app.node.try_get_context("enable_endpoint")),
)

cdk.Tags.of(stack).add("Project", "recommender")
cdk.Tags.of(stack).add("ManagedBy", "cdk")
cdk.Tags.of(stack).add("Repo", "aws-lambda-functions")

app.synth()
