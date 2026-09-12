# aws-lambda-functions

AWS project templates — infrastructure as code plus working application code for 13 reference
architectures, from static site hosting through agentic LLM assistants.

The original content of this repo (Lambda functions for Secrets Manager create/delete based on
role access) is unchanged; the new templates live under `projects/`.

## Projects

| # | Project | IaC | Runtime | Core services |
|---|---------|-----|---------|---------------|
| 01 | [Static website on S3](projects/01-static-website-s3) | Terraform | — | S3, CloudFront, ACM, Route 53 |
| 02 | [Python web app on Elastic Beanstalk](projects/02-beanstalk-python-webapp) | Terraform | Python (Flask) | Elastic Beanstalk, EC2, ALB |
| 03 | [SQL Server on Amazon RDS](projects/03-rds-sqlserver) | Terraform | — | RDS SQL Server, VPC, Secrets Manager |
| 04 | [Serverless image processing](projects/04-serverless-image-processing) | AWS SAM | Python | S3, Lambda, SQS, DynamoDB |
| 05 | [Chatbot with Amazon Lex](projects/05-lex-chatbot) | Terraform | Python | Lex V2, Lambda, DynamoDB |
| 06 | [Fraud detection on SageMaker](projects/06-sagemaker-fraud-detection) | AWS CDK (Python) | Python | SageMaker, S3, Lambda, API Gateway |
| 07 | [Recommendation system (MXNet)](projects/07-sagemaker-recommender-mxnet) | AWS CDK (Python) | Python | SageMaker, S3 |
| 08 | [Image classification](projects/08-sagemaker-image-classification) | AWS CDK (Python) | Python | SageMaker, S3, Lambda |
| 09 | [Multimodal RAG on Bedrock](projects/09-multimodal-rag-bedrock) | AWS CDK (Python) | Python | Bedrock, OpenSearch Serverless, S3, Lambda |
| 10 | [Agentic LLM assistant](projects/10-agentic-llm-assistant) | AWS CDK (Python) | Python | Bedrock Agents, Lambda, API Gateway, DynamoDB |
| 11 | [Fullstack app: ECS + Terraform + CodePipeline](projects/11-fullstack-ecs-terraform-codepipeline) | Terraform | Node.js + React | ECS Fargate, ALB, ECR, CodePipeline, CodeBuild |
| 12 | [Automated CloudWatch alarm reporting](projects/12-cloudwatch-alarm-reporting) | AWS SAM | Python | EventBridge, Lambda, CloudWatch, SES, S3 |
| 13 | [Containerized web app on ECS Fargate](projects/13-ecs-fargate-webapp) | Terraform | Node.js | ECS Fargate, ALB, ECR, CodeDeploy (blue/green) |

IaC choice is per project — Terraform for infrastructure-heavy stacks, SAM for pure serverless
event pipelines, CDK for the ML/GenAI stacks where L2 constructs save the most code. See
[docs/CONVENTIONS.md](docs/CONVENTIONS.md) for the rationale and the shared layout every project follows.

## Quick start

```bash
# prerequisites (macOS)
brew install terraform awscli node python@3.12
brew install --cask docker            # projects 04, 11, 13
pipx install aws-sam-cli aws-cdk-local # or: npm i -g aws-cdk

aws configure --profile aws-projects
export AWS_PROFILE=aws-projects
export AWS_REGION=ap-south-1

cd projects/01-static-website-s3
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init && terraform plan
```

Every project folder has its own README with prerequisites, deploy steps, a cost note, and teardown.

## Verifying before you deploy

```bash
./scripts/verify.sh            # structure, Terraform fmt/validate, cfn-lint, syntax across every project
./scripts/verify.sh --tests    # also runs the unit test suites
```

The script uses whatever is installed and skips the rest, so it is useful with nothing but Python
and Node. To get the full sweep:

```bash
brew install terraform node python@3.12
pip install pytest 'moto[s3,dynamodb]' boto3 Pillow cfn-lint
```

### Test suites

| Project | Tests | Needs AWS? |
|---|---|---|
| 04 | Image processing: derivative generation, size limits, rejection paths (moto) | no |
| 05 | Lex dialog validation: past dates, closed days, opening hours | no |
| 09 | Chunking and reciprocal rank fusion | no |
| 10 | Agent tools: validation, idempotency, error shapes, no PII leakage (moto) | no |
| 11 | API routes, 404/400 handling, in-memory store | no |
| 12 | Alarm aggregation, HTML escaping, CSV sections | no |
| 13 | Health/readiness/draining, metric cardinality, HTML escaping | no |

Every suite runs offline — `moto` fakes S3 and DynamoDB, and the ML/GenAI projects keep their pure
logic (chunking, fusion, dialog rules) separate from the AWS calls precisely so it can be tested
this way.

## Status

All 13 folders contain working core infrastructure and application code. Nothing here has been
applied to an AWS account — review `terraform plan` / `sam deploy --guided` / `cdk diff` output
before creating resources, and mind the cost notes. The expensive ones to leave running:

| What | Rough idle cost |
|---|---|
| OpenSearch Serverless (project 09) | ~$175/month — 2 OCU minimum, charged when idle |
| SageMaker real-time endpoint (06, 07, 08) | ~$83/month per `ml.m5.large` |
| RDS SQL Server (03) | ~$25-30/month Express, 3x that for Standard |
| NAT gateway (11, 13) | ~$32/month each, plus data processing |
| ALB (02, 11, 13) | ~$16/month each |

Serverless projects (01, 04, 05, 10, 12) cost essentially nothing when idle.
