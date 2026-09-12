# 05 — Creating a Chatbot with AWS Lex

An Amazon Lex V2 bot ("BookingBot") that takes appointment bookings and answers status questions,
with a Python Lambda code hook doing validation and fulfillment against DynamoDB.

```
Channel (console test / web / Connect) ──> Lex V2 bot alias
                                              │  DialogCodeHook   (slot validation)
                                              └─ FulfillmentCodeHook ──> Lambda ──> DynamoDB bookings
```

## Intents

| Intent | Sample utterances | Slots |
|--------|------------------|-------|
| `BookAppointment` | "book an appointment", "I'd like to see a dentist on Friday" | `AppointmentType`, `Date`, `Time`, `Name` |
| `CheckBooking` | "what's the status of my booking", "check booking ABC123" | `BookingRef` |
| `CancelBooking` | "cancel my appointment" | `BookingRef` |
| `FallbackIntent` | built-in | — |

Slot validation lives in the Lambda, not in Lex: the dialog code hook rejects past dates and
out-of-hours times and re-elicits the slot with a specific message, which is the difference between
a bot that feels helpful and one that just repeats itself.

## Layout

```
infra/          Terraform: bot, locale, slot types, intents, slots, version, alias, Lambda wiring
src/fulfillment Lambda code hook (dialog + fulfillment) and its DynamoDB access
tests/          pytest tests for the dialog logic, no AWS needed
```

## Prerequisites

- Terraform >= 1.6 with AWS provider >= 5.60 (Lex V2 `aws_lexv2models_*` resources)
- Lex V2 is not available in every region — `ap-south-1`, `us-east-1`, `eu-west-1` are safe choices

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
terraform output bot_id
```

Then in the console: **Amazon Lex → BookingBot → Test** (or use the CLI below). After any intent
change Terraform rebuilds the locale and publishes a new version; the alias moves to it.

```bash
aws lexv2-runtime recognize-text \
  --bot-id "$(terraform -chdir=infra output -raw bot_id)" \
  --bot-alias-id "$(terraform -chdir=infra output -raw bot_alias_id)" \
  --locale-id en_US --session-id demo-1 \
  --text "I want to book a dental appointment tomorrow at 3pm"
```

## Building the locale

Lex needs an explicit build after intents change. Terraform creates the resources; the build is
triggered by `null_resource.build_locale` (it shells out to `aws lexv2-models build-bot-locale` and
polls until `Built`). If you prefer to build by hand, set `auto_build = false` and run:

```bash
aws lexv2-models build-bot-locale --bot-id <id> --bot-version DRAFT --locale-id en_US
```

## Extending

- **More intents**: copy an `aws_lexv2models_intent` block; utterances are `sample_utterance` blocks.
- **Web chat**: front the runtime API with API Gateway + `recognize-text`, or use Amazon Connect /
  Lex web UI. The Lambda does not care which channel it is called from.
- **Multi-language**: add another `aws_lexv2models_bot_locale` with `locale_id = "hi_IN"` and
  duplicate the intents under it.

## Cost

Lex V2 charges per request: $0.004 per text request, $0.0065 per speech request, with 10,000 text
requests/month free for the first year. Lambda and DynamoDB on-demand are pennies at this volume.

## Teardown

```bash
cd infra && terraform destroy
```
