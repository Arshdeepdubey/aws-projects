#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.assistant_stack import AgenticAssistantStack

app = cdk.App()


def context(key: str, default):
    value = app.node.try_get_context(key)
    return default if value is None else value


stack = AgenticAssistantStack(
    app,
    "AgenticAssistantStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    project=context("project", "agentic-assistant"),
    environment_name=context("environment", "dev"),
    foundation_model=context("foundation_model", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
    idle_session_ttl_seconds=int(context("idle_session_ttl_seconds", 900)),
    guardrail_blocked_message=context("guardrail_blocked_message", "I can't help with that."),
)

cdk.Tags.of(stack).add("Project", "agentic-assistant")
cdk.Tags.of(stack).add("ManagedBy", "cdk")
cdk.Tags.of(stack).add("Repo", "aws-lambda-functions")

app.synth()
