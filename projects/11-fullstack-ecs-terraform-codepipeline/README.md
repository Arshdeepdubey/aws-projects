# 11 — Building a Fullstack App Using ECS, Terraform, and CodePipeline

A two-service application (Node.js API + React web) on ECS Fargate behind one ALB, with a
CodePipeline that builds both images, pushes them to ECR, and rolls the services forward on every
push to `main`.

```
GitHub ──(CodeStar connection)──> CodePipeline
                                     ├─ Build   ──> CodeBuild ──> ECR (api, web) + imagedefinitions.json
                                     └─ Deploy  ──> ECS rolling update (circuit breaker + auto rollback)

Internet ──> ALB :443/:80
              ├─ /api/*  ──> ECS service: api  (Fargate, private subnets)
              └─ /*      ──> ECS service: web  (Fargate, private subnets)
                                   │
                             NAT gateway ──> ECR, CloudWatch, Secrets Manager
```

## What this template gets right

**Deployment circuit breaker with rollback.** `deployment_circuit_breaker { enable = true, rollback = true }`
means a container that fails its health check rolls back automatically instead of leaving the
service half-deployed while CodePipeline reports success.

**Image tags are immutable and content-addressed.** Images are tagged with the commit SHA, never
`latest`. ECR has `image_tag_mutability = "IMMUTABLE"`, so a tag always means one specific build —
without it, "roll back to the previous image" is not a well-defined operation.

**The task definition is built by the pipeline, not pinned in Terraform.** Terraform owns the
service and its initial task definition; the deploy stage registers new revisions. `lifecycle {
ignore_changes = [task_definition, desired_count] }` on the service stops the next `terraform apply`
from reverting a deployment or fighting autoscaling.

**Secrets come from Secrets Manager at task start**, injected as `secrets` in the container
definition — never baked into the image or passed as plain `environment`.

**One ALB, two target groups, path-based routing.** Cheaper than two load balancers and it keeps
the API same-origin with the web app, so no CORS configuration and no preflight round trip.

## Layout

```
infra/
  network.tf     VPC, public/private subnets across 2 AZs, NAT, VPC endpoints
  alb.tf         ALB, listeners, target groups, path rules, HTTPS (optional ACM)
  ecr.tf         Two repositories with lifecycle policies and image scanning
  ecs.tf         Cluster, task definitions, services, autoscaling, Container Insights
  pipeline.tf    CodeStar connection, CodePipeline, CodeBuild projects, artifact bucket
  iam.tf         Execution/task roles, pipeline and build roles
  monitoring.tf  Alarms on 5xx, unhealthy hosts, CPU, and failed deployments
app/api/         Express API with /api/health, /api/todos (DynamoDB), graceful shutdown
app/web/         React + Vite app served by nginx, built in a multi-stage Dockerfile
buildspecs/      CodeBuild specs for api and web
```

## Prerequisites

- Terraform >= 1.6, AWS CLI v2, Docker (to build locally)
- A GitHub repository containing this project, and a **CodeStar connection** in the console:
  Developer Tools → Settings → Connections → Create connection → GitHub → install the app.
  The connection starts in `PENDING` and must be completed in the console once; Terraform cannot
  finish the OAuth handshake for you.

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # set github_repository, connection_arn, domain
terraform init
terraform apply                                 # ~10 minutes (NAT and ALB dominate)

terraform output alb_url
```

The first `apply` starts services with a placeholder image (`public.ecr.aws/nginx/nginx`) so the
ALB has healthy targets before any build exists. The first pipeline run replaces it.

### Trigger a build

```bash
git push origin main                 # the connection webhook starts the pipeline
# or
aws codepipeline start-pipeline-execution --name fullstack-dev-pipeline
```

## Run it locally first

```bash
cd app/api && npm install && npm run dev      # http://localhost:3000/api/health
cd app/web && npm install && npm run dev      # http://localhost:5173, proxies /api to :3000
docker compose up --build                     # both, as they run in production
```

## Rollback

```bash
# find the previous task definition revision
aws ecs describe-services --cluster fullstack-dev --services fullstack-dev-api \
  --query 'services[0].deployments'

aws ecs update-service --cluster fullstack-dev --service fullstack-dev-api \
  --task-definition fullstack-dev-api:41 --force-new-deployment
```

Because tags are immutable and the SHA is in the revision, that command is unambiguous.

## Cost

The floor is the NAT gateway (~$32/month per AZ plus data) and the ALB (~$16/month). Two Fargate
tasks at 0.25 vCPU / 0.5 GB are ~$18/month. Set `single_nat_gateway = true` in dev to halve the
NAT cost; set `assign_public_ip = true` and `enable_nat_gateway = false` to remove NAT entirely
at the cost of putting tasks in public subnets.

## Teardown

```bash
cd infra && terraform destroy
```

ECR repositories are created with `force_delete = true` so `destroy` does not fail on stored images.
