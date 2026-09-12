# 12 — Building an Automated CloudWatch Alarm Reporting System

Two moving parts: a recorder that captures every alarm state change as it happens, and a scheduled
reporter that turns the last 24 hours (or 7 days) into an HTML + CSV report delivered by email and
archived in S3.

```
CloudWatch alarms ──(state change event)──> EventBridge rule ──> Recorder Lambda ──> DynamoDB history
                                                                                        │
EventBridge schedule (daily 08:00 IST) ──> Reporter Lambda ──> HTML + CSV ──> S3 archive │
                                                    └────────> SES email  <──────────────┘
```

Reading alarm state at report time only tells you what is broken *now*. The recorder is what lets
the report say "checkout-latency flapped 14 times overnight" — the number that actually starts a
conversation.

## Layout

```
template.yaml        SAM: rules, functions, table, bucket, SES identity reference
src/recorder/        Alarm state-change handler -> DynamoDB (TTL'd history)
src/reporter/        Scheduled report builder: query history + describe_alarms, render, send
src/reporter/render.py  HTML and CSV rendering, kept separate so it is unit-testable
tests/               pytest: rendering and aggregation logic, no AWS
```

## Prerequisites

- AWS SAM CLI, Python 3.12
- A verified SES identity (domain or address) in the deployment region, and if SES is still in
  sandbox mode the recipients must be verified too:
  `aws ses verify-email-identity --email-address you@example.com`

## Deploy

```bash
sam build
sam deploy --guided \
  --parameter-overrides SenderEmail=alerts@example.com RecipientEmails=you@example.com
```

Then trigger a report immediately instead of waiting for the schedule:

```bash
aws lambda invoke --function-name alarm-reporting-dev-reporter \
  --payload '{"windowHours": 24}' --cli-binary-format raw-in-base64-out /dev/stdout
```

## What the report contains

- **Now**: count of alarms in ALARM / INSUFFICIENT_DATA / OK, with the ALARM ones listed first,
  newest state change at the top.
- **Window**: every state transition in the period, grouped by alarm, with a flap count.
- **Noise ranking**: alarms sorted by transition count — the ones to retune or delete.
- **Coverage gaps**: alarms with no actions configured, and alarms that have been in
  INSUFFICIENT_DATA for the whole window (usually a metric that stopped being published).

CSV is attached for spreadsheets; HTML is the email body and the S3 archive copy.

## Configuration

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `ScheduleExpression` | `cron(30 2 * * ? *)` | 08:00 IST daily (EventBridge is UTC) |
| `WindowHours` | `24` | Reporting window |
| `AlarmNamePrefix` | `""` | Restrict to alarms with this prefix |
| `HistoryTtlDays` | `90` | DynamoDB TTL on recorded transitions |
| `SenderEmail` / `RecipientEmails` | — | SES sender and comma-separated recipients |

## Cost

Well inside free tier for a typical account: a handful of Lambda invocations a day, DynamoDB
on-demand writes on alarm transitions, SES at $0.10 per 1,000 emails, S3 pennies.

## Teardown

```bash
sam delete
```
