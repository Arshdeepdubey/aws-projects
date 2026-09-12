# Conventions

## Folder layout

Every project follows the same shape, with the IaC directory named after the tool:

```
NN-project-name/
├── README.md              # what it builds, prereqs, deploy, cost, teardown
├── Makefile               # init / plan / deploy / destroy shortcuts
├── infra/                 # terraform | sam | cdk  (see per-project README)
└── src/ | app/            # application code (handlers, web app, notebooks)
```

## Why the IaC tool differs per project

| Tool | Used for | Reason |
|------|----------|--------|
| Terraform | 01, 02, 03, 05, 11, 13 | Long-lived infrastructure, multi-service wiring, existing team tooling, and project 11 requires it by definition. |
| AWS SAM | 04, 12 | Pure event-driven Lambda pipelines — SAM's `Events` shorthand and `sam local invoke` beat hand-written IAM and trigger plumbing. |
| AWS CDK (Python) | 06, 07, 08, 09, 10 | ML/GenAI stacks need loops, conditionals and asset bundling; CDK L2 constructs for SageMaker endpoints, Bedrock and OpenSearch Serverless cut hundreds of lines. |

## Naming and tagging

Resources are named `${var.project}-${var.environment}-<resource>` and every stack applies these
tags: `Project`, `Environment`, `ManagedBy` (`terraform` | `sam` | `cdk`), `Repo`.

## Variables

- Terraform: `variables.tf` with defaults where safe, `terraform.tfvars.example` committed,
  `terraform.tfvars` git-ignored.
- SAM: `template.yaml` `Parameters` plus a `samconfig.toml` per environment.
- CDK: `cdk.json` context plus environment variables; no hardcoded account IDs.

## State

Terraform projects default to local state so they run out of the box. For anything shared, move to
S3 + DynamoDB locking by uncommenting the `backend "s3"` block in `versions.tf` and running
`terraform init -migrate-state`.

## Security defaults

- No public S3 buckets — CloudFront with Origin Access Control instead.
- Secrets in Secrets Manager or SSM Parameter Store, never in `.tf` files or environment defaults.
- Encryption at rest on by default (S3 SSE, RDS storage encryption, DynamoDB SSE).
- Least-privilege IAM: inline policies scoped to the specific ARNs each function touches.
- Security groups reference other security groups rather than CIDR blocks wherever possible.

## Testing

Application logic is written so it can be tested without an AWS account:

- Pure logic (chunking, ranking, dialog rules, validation, rendering) lives in its own module with
  no `boto3` import at call time, so a test imports it directly.
- Anything that does touch AWS is tested against `moto`, never against a real account.
- Handlers return errors as data where a caller has to act on them (Bedrock agent tools, Lex code
  hooks) and raise only where a retry is the right response (SQS consumers).

Run everything from the repository root:

```bash
./scripts/verify.sh --tests
```

Per project: `make test` (pytest) or `npm test` (node --test) inside the app directory.

## Verification before deploying

`scripts/verify.sh` runs, for every project: required files, `terraform fmt -check` and
`terraform validate` (with `-backend=false`, so no credentials and no state), `cfn-lint` on SAM
templates, and syntax checks on Python, JavaScript, shell, JSON and YAML. Missing tools are
skipped rather than failed, so it is useful in a bare environment and complete in CI.

## Regions

Defaults are `ap-south-1` except where a service or model is region-limited (Bedrock projects
default to `us-east-1`). Each README notes the constraint.
