"""Bedrock Agent, its tools, its guardrail, and the API in front of it."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_bedrock as bedrock,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
)
from constructs import Construct

AGENT_INSTRUCTION = """You are the customer operations assistant for an online retailer.

You help customers with orders, delivery, returns and escalations. You have tools that read and
write real records — use them rather than guessing, and never invent an order number, date, price
or policy.

How to work:
- Identify what the customer actually needs before calling anything. If an order number is missing
  and they have not given one, ask for it, or use list_orders_for_customer when you have their id.
- Chain tools when a question needs it: check the order, then check the policy, then compare.
- Do arithmetic with estimate_shipping rather than in your head.
- Before any action that changes something (starting a return, opening a ticket), state exactly
  what you are about to do and wait for the customer to confirm.
- If a tool returns an error, explain the problem in plain language and suggest the next step. Do
  not retry the same call with the same arguments.
- If something is outside orders, delivery, returns or account questions, say so and offer to open
  a ticket for a human.

Tone: brief, warm, concrete. Give the specific date, amount or status rather than a summary of it.
Never disclose another customer's data, internal notes, or full payment card numbers."""


class AgenticAssistantStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        environment_name: str,
        foundation_model: str,
        idle_session_ttl_seconds: int = 900,
        guardrail_blocked_message: str = "I can't help with that.",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        name = f"{project}-{environment_name}"

        # ------------------------------------------------------------- data
        self.orders = dynamodb.Table(
            self,
            "OrdersTable",
            table_name=f"{name}-orders",
            partition_key=dynamodb.Attribute(name="orderId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.orders.add_global_secondary_index(
            index_name="customer-orderedAt-index",
            partition_key=dynamodb.Attribute(name="customerId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="orderedAt", type=dynamodb.AttributeType.STRING),
        )

        self.tickets = dynamodb.Table(
            self,
            "TicketsTable",
            table_name=f"{name}-tickets",
            partition_key=dynamodb.Attribute(name="ticketId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Transcript per session. TTL'd: a conversation log is a liability once
        # it stops being useful.
        self.conversations = dynamodb.Table(
            self,
            "ConversationsTable",
            table_name=f"{name}-conversations",
            partition_key=dynamodb.Attribute(name="sessionId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="turnAt", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            time_to_live_attribute="expiresAt",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------ tools
        self.actions_fn = lambda_.Function(
            self,
            "ActionsFunction",
            function_name=f"{name}-actions",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src/actions"),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "ORDERS_TABLE": self.orders.table_name,
                "TICKETS_TABLE": self.tickets.table_name,
                "LOG_LEVEL": "INFO",
            },
        )
        self.orders.grant_read_write_data(self.actions_fn)
        self.tickets.grant_read_write_data(self.actions_fn)

        # --------------------------------------------------------- guardrail
        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail",
            name=f"{name}-guardrail",
            description="Keeps the assistant on topic and protects customer data",
            blocked_input_messaging=guardrail_blocked_message,
            blocked_outputs_messaging=guardrail_blocked_message,
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=filter_type, input_strength="HIGH", output_strength="HIGH"
                    )
                    for filter_type in ("SEXUAL", "VIOLENCE", "HATE", "INSULTS", "PROMPT_ATTACK")
                ]
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="LegalAdvice",
                        type="DENY",
                        definition="Advice on legal rights, disputes, claims or litigation.",
                        examples=[
                            "Can I sue you for the late delivery?",
                            "What are my statutory rights here?",
                        ],
                    ),
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="CompetitorPricing",
                        type="DENY",
                        definition="Comparisons with, or advice about, competitors' prices or products.",
                        examples=["Is this cheaper on another site?"],
                    ),
                ]
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="EMAIL", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PHONE", action="ANONYMIZE"),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_NUMBER", action="BLOCK"),
                ]
            ),
        )

        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description="Deployed by CDK",
        )

        # ------------------------------------------------------------ agent
        agent_role = iam.Role(
            self,
            "AgentRole",
            role_name=f"AmazonBedrockExecutionRoleForAgents_{environment_name}",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{self.region}:{self.account}:agent/*"
                    },
                },
            ),
        )
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/{foundation_model}",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                ],
            )
        )
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[self.guardrail.attr_guardrail_arn],
            )
        )

        self.agent = bedrock.CfnAgent(
            self,
            "Agent",
            agent_name=name,
            agent_resource_role_arn=agent_role.role_arn,
            foundation_model=foundation_model,
            instruction=AGENT_INSTRUCTION,
            description="Customer operations assistant",
            idle_session_ttl_in_seconds=idle_session_ttl_seconds,
            auto_prepare=True,  # re-prepares the agent whenever tools or instructions change
            guardrail_configuration=bedrock.CfnAgent.GuardrailConfigurationProperty(
                guardrail_identifier=self.guardrail.attr_guardrail_id,
                guardrail_version=self.guardrail_version.attr_version,
            ),
            action_groups=[
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name="customer-operations",
                    description="Order lookup, returns, shipping estimates and escalation",
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=self.actions_fn.function_arn
                    ),
                    action_group_state="ENABLED",
                    function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                        functions=_tool_definitions()
                    ),
                ),
                # Lets the agent ask a clarifying question instead of guessing at
                # a missing order number.
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name="UserInputAction",
                    parent_action_group_signature="AMAZON.UserInput",
                    action_group_state="ENABLED",
                ),
            ],
        )

        self.actions_fn.add_permission(
            "AllowBedrockAgentInvoke",
            principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=f"arn:aws:bedrock:{self.region}:{self.account}:agent/{self.agent.attr_agent_id}",
        )

        self.agent_alias = bedrock.CfnAgentAlias(
            self,
            "AgentAlias",
            agent_alias_name=environment_name,
            agent_id=self.agent.attr_agent_id,
            description="Alias used by the chat API",
        )

        # -------------------------------------------------------------- api
        self.chat_fn = lambda_.Function(
            self,
            "ChatFunction",
            function_name=f"{name}-chat",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../src/api"),
            timeout=Duration.minutes(5),
            memory_size=512,
            log_retention=logs.RetentionDays.TWO_WEEKS,
            environment={
                "AGENT_ID": self.agent.attr_agent_id,
                "AGENT_ALIAS_ID": self.agent_alias.attr_agent_alias_id,
                "CONVERSATIONS_TABLE": self.conversations.table_name,
                "TRANSCRIPT_TTL_DAYS": "30",
                "LOG_LEVEL": "INFO",
            },
        )
        self.conversations.grant_read_write_data(self.chat_fn)
        self.chat_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeAgent"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:agent-alias/"
                    f"{self.agent.attr_agent_id}/{self.agent_alias.attr_agent_alias_id}"
                ],
            )
        )

        http_api = apigw.HttpApi(self, "ChatApi", api_name=f"{name}-api")
        http_api.add_routes(
            path="/chat",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("ChatIntegration", self.chat_fn),
        )
        http_api.add_routes(
            path="/sessions/{sessionId}",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration("HistoryIntegration", self.chat_fn),
        )

        # ---------------------------------------------------------- outputs
        cdk.CfnOutput(self, "AgentId", value=self.agent.attr_agent_id)
        cdk.CfnOutput(self, "AgentAliasId", value=self.agent_alias.attr_agent_alias_id)
        cdk.CfnOutput(self, "GuardrailId", value=self.guardrail.attr_guardrail_id)
        cdk.CfnOutput(self, "OrdersTable", value=self.orders.table_name)
        cdk.CfnOutput(self, "TicketsTable", value=self.tickets.table_name)
        cdk.CfnOutput(self, "ApiUrl", value=http_api.api_endpoint)


def _parameter(description: str, type_: str = "string", required: bool = True):
    return bedrock.CfnAgent.ParameterDetailProperty(
        type=type_, description=description, required=required
    )


def _tool_definitions() -> list:
    """Function schema for the action group.

    Descriptions here are prompt engineering, not documentation — they are what the
    model reads when deciding whether a tool applies. Say when to use it and what it
    returns, not how it is implemented.
    """
    return [
        bedrock.CfnAgent.FunctionProperty(
            name="get_order",
            description=(
                "Look up one order by its id. Returns status, items, total, order date and "
                "estimated or actual delivery date. Use whenever the customer gives an order number."
            ),
            parameters={"orderId": _parameter("Order id, e.g. ORD-10428")},
        ),
        bedrock.CfnAgent.FunctionProperty(
            name="list_orders_for_customer",
            description=(
                "List a customer's recent orders, newest first. Use when the customer does not "
                "know their order number but you have their customer id."
            ),
            parameters={
                "customerId": _parameter("Customer id, e.g. CUST-1042"),
                "limit": _parameter("How many orders to return, default 5", "integer", False),
            },
        ),
        bedrock.CfnAgent.FunctionProperty(
            name="get_return_policy",
            description=(
                "Return the returns window and conditions for a product category. Use before "
                "telling a customer whether they can return something."
            ),
            parameters={"category": _parameter("Product category, e.g. electronics, apparel")},
        ),
        bedrock.CfnAgent.FunctionProperty(
            name="estimate_shipping",
            description=(
                "Calculate shipping cost and delivery date for a destination, weight and speed. "
                "Always use this instead of calculating yourself."
            ),
            parameters={
                "destinationPostcode": _parameter("Destination postcode"),
                "weightKg": _parameter("Total weight in kilograms", "number"),
                "speed": _parameter("standard | express | overnight", "string", False),
            },
        ),
        bedrock.CfnAgent.FunctionProperty(
            name="start_return",
            description=(
                "Start a return for an order. This changes the order and emails a label, so "
                "confirm the order id and reason with the customer first."
            ),
            parameters={
                "orderId": _parameter("Order id to return"),
                "reason": _parameter("Why the customer is returning it"),
                "idempotencyKey": _parameter("Unique key for this return request", "string", False),
            },
            require_confirmation="ENABLED",
        ),
        bedrock.CfnAgent.FunctionProperty(
            name="create_ticket",
            description=(
                "Escalate to a human agent by opening a support ticket. Use when the request is "
                "outside your tools or the customer asks for a person. Confirm before creating."
            ),
            parameters={
                "customerId": _parameter("Customer id"),
                "summary": _parameter("One-line summary of the issue"),
                "details": _parameter("Everything the human will need", "string", False),
                "priority": _parameter("low | normal | high", "string", False),
                "idempotencyKey": _parameter("Unique key for this ticket", "string", False),
            },
            require_confirmation="ENABLED",
        ),
    ]
