# 10 — Building an Agentic LLM Assistant on AWS

A customer-operations assistant built on **Amazon Bedrock Agents**: it plans, calls real tools
(order lookup, returns, shipping estimates, ticket creation), remembers the conversation, and is
fenced in by a Bedrock Guardrail.

```
Client ──> HTTP API ──> Chat Lambda ──> bedrock-agent-runtime:InvokeAgent
                            │                     │
                   DynamoDB conversations         ├─ Guardrail (PII, topics, profanity)
                                                  ├─ Action group ──> Tools Lambda ──> DynamoDB orders/tickets
                                                  └─ (optional) Knowledge Base ──> OpenSearch Serverless
```

## What makes it *agentic* rather than a chatbot with a database call

The model decides **which** tools to call, **in what order**, and when it has enough to answer.
"Where's my order and can I still return it?" becomes: `get_order` → `get_return_policy` →
compare delivery date against the policy window → answer. None of that sequencing is in your code.

What *is* in your code, and what this template gets right:

- **Tools return data, never prose.** `get_order` returns a JSON record; it does not write a
  sentence for the user. The moment tools return prose, the model starts parroting it and you lose
  control of tone and of what is disclosed.
- **Every tool is idempotent or explicitly confirmed.** `create_ticket` takes an
  `idempotencyKey` so a retried agent step does not open three tickets.
- **Write actions require confirmation.** `create_ticket` and `start_return` are declared with
  `requireConfirmation: ENABLED`, so the agent must surface the action and get a user "yes" before
  it executes. This is the single most important safety property of an agent that can act.
- **Tool errors are returned as data, not exceptions.** A 500 tells the agent nothing;
  `{"error": "order_not_found", "hint": "ask the customer to confirm the order number"}`
  lets it recover in the same turn.
- **The session id is the memory.** Conversation state lives in Bedrock's session plus a DynamoDB
  transcript; nothing is kept in Lambda globals, which would leak between users.

## Tools the agent has

| Tool | Kind | Notes |
|------|------|-------|
| `get_order` | read | Order status, items, delivery estimate |
| `list_orders_for_customer` | read | Recent orders, newest first |
| `get_return_policy` | read | Window and conditions by category |
| `estimate_shipping` | read | Deterministic calculation, no model arithmetic |
| `start_return` | **write** | Requires confirmation; idempotent on `idempotencyKey` |
| `create_ticket` | **write** | Requires confirmation; escalates to a human queue |

## Layout

```
infra/           CDK: agent, alias, action group, guardrail, tables, chat Lambda, HTTP API
src/actions/     Tools Lambda — one function per tool, strict input validation
src/api/         Chat Lambda — invokes the agent, streams traces to logs, persists the transcript
src/seed/        seed_data.py — sample orders so the tools have something to return
schemas/         actions.json — OpenAPI 3 schema for the action group
scripts/chat.py  Terminal chat client against the deployed API
tests/           Tool-logic tests, no AWS
```

## Prerequisites

- AWS CDK v2, Python 3.11+
- Bedrock model access for a Claude model **and** the agent-capable variant in your region
  (`us-east-1` by default — Agents are not available everywhere)
- `aws bedrock list-foundation-models --by-inference-type ON_DEMAND` to confirm

## Deploy

```bash
cd infra && pip install -r requirements.txt
cdk deploy AgenticAssistantStack

export API_URL=$(aws cloudformation describe-stacks --stack-name AgenticAssistantStack \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)

python ../src/seed/seed_data.py --orders-table agentic-assistant-dev-orders
python ../scripts/chat.py --api "$API_URL"
```

After a change to the agent's instructions or tools, the agent must be **prepared** again — CDK
does this through `CfnAgent.auto_prepare`, but a manual run is:

```bash
aws bedrock-agent prepare-agent --agent-id <id>
```

## Watching it think

The chat Lambda requests the full trace (`enableTrace=True`) and logs the model's rationale, each
tool invocation and its result. That trace is the debugger for an agent:

```bash
aws logs tail /aws/lambda/agentic-assistant-dev-chat --follow --format short | grep TRACE
```

Set `"returnTrace": true` in the request body to get the trace back in the API response while
developing. Leave it off in production — traces contain the model's internal reasoning.

## Guardrail

The stack creates a Bedrock Guardrail that blocks prompts about competitor pricing and legal
advice, masks emails and phone numbers in output, and refuses to disclose payment card numbers.
The refusal message is configurable in `cdk.json`. Guardrails are enforced by the service, not by
the prompt — a jailbreak in the user's message cannot switch them off.

## Cost

Pay-per-token only, plus DynamoDB on-demand. A typical multi-tool exchange runs 3,000-8,000 input
tokens once the agent's tool schemas and history are included, so budget more per turn than a
plain chat call. No idle cost — nothing here runs when no one is chatting.

## Teardown

```bash
cd infra && cdk destroy AgenticAssistantStack
```
