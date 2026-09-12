#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.rag_stack import MultimodalRagStack

app = cdk.App()


def context(key: str, default: str) -> str:
    return app.node.try_get_context(key) or default


stack = MultimodalRagStack(
    app,
    "MultimodalRagStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    project=context("project", "multimodal-rag"),
    environment_name=context("environment", "dev"),
    bedrock_region=context("bedrock_region", "us-east-1"),
    index_name=context("index_name", "multimodal-rag"),
    models={
        "text_embedding": context("text_embedding_model", "amazon.titan-embed-text-v2:0"),
        "multimodal_embedding": context("multimodal_embedding_model", "amazon.titan-embed-image-v1"),
        "generation": context("generation_model", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
        "vision": context("vision_model", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
    },
)

cdk.Tags.of(stack).add("Project", "multimodal-rag")
cdk.Tags.of(stack).add("ManagedBy", "cdk")
cdk.Tags.of(stack).add("Repo", "aws-lambda-functions")

app.synth()
