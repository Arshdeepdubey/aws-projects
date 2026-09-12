# 13 — Building a Containerized Web Application using Amazon ECS and AWS Fargate

A single containerized web application on ECS Fargate, deployed **blue/green with CodeDeploy** so
every release goes to a fresh target group, gets health-checked and (optionally) canary-shifted
before it takes traffic, with a one-command rollback.

```
Internet ──> ALB ──┬─ listener :80/:443 ──> target group BLUE  (current)
                   └─ test listener :8080 ──> target group GREEN (new revision)
                                   │
                        CodeDeploy shifts traffic 10% → 100%
                                   │
                             ECS Fargate service (private subnets, NAT)
                                   │
                            CloudWatch Logs + Container Insights
```

Where project 11 shows a rolling update driven by CodePipeline, this one shows the deployment
strategy you reach for when a bad release must never serve a single user request.

## Rolling vs blue/green — the actual trade

| | Rolling (project 11) | Blue/green (here) |
|---|---|---|
| Extra capacity during deploy | Some (200% max) | Full second set of tasks |
| Bad release reaches users | Briefly, until health checks fail | Never, if the test listener catches it |
| Rollback | Redeploy previous task definition (minutes) | Shift traffic back (seconds) |
| Complexity | Low | CodeDeploy app, deployment group, two target groups |
| Cost during deploy | ~1.2× | 2× for the bake window |

Blue/green earns its complexity when a rollback has to be instant. For an internal tool, rolling
is the better default.

## Layout

```
infra/
  network.tf      VPC, subnets, NAT, S3 endpoint
  alb.tf          ALB, production + test listeners, blue and green target groups
  ecs.tf          Cluster, task definition, service (CODE_DEPLOY controller), autoscaling
  codedeploy.tf   CodeDeploy application, deployment group, traffic-shifting config, alarms
  iam.tf          Execution, task, and CodeDeploy roles
app/              Node.js app: health, readiness, metrics, graceful shutdown, version endpoint
scripts/
  build_and_push.sh   Build, tag with the git SHA, push to ECR
  deploy.sh           Register a task definition revision and start a CodeDeploy deployment
  rollback.sh         Stop and roll back the in-flight deployment
```

## Prerequisites

- Terraform >= 1.6, Docker, AWS CLI v2, `jq`

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply          # ~8 minutes

cd ..
./scripts/build_and_push.sh                # builds, tags with the git SHA, pushes to ECR
./scripts/deploy.sh                        # registers a revision, starts blue/green traffic shift

curl "$(terraform -chdir=infra output -raw alb_url)/health"
curl "$(terraform -chdir=infra output -raw alb_url)/version"
```

### Watching a deployment

```bash
aws deploy get-deployment --deployment-id d-XXXXXXXXX \
  --query 'deploymentInfo.{status:status,traffic:deploymentOverview}'
```

Test the green fleet before it takes production traffic, on the test listener:

```bash
curl "http://$(terraform -chdir=infra output -raw alb_dns_name):8080/version"
```

### Rolling back

```bash
./scripts/rollback.sh                 # in-flight deployment
# or, after a completed deployment, redeploy the previous image:
IMAGE_TAG=sha-abc1234 ./scripts/deploy.sh
```

`terraform.tfvars` controls the shift: `deployment_config` of
`CodeDeployDefault.ECSCanary10Percent5Minutes` (default), `ECSLinear10PercentEvery1Minutes`, or
`ECSAllAtOnce`. The deployment group also auto-rolls-back when the 5xx or unhealthy-host alarms
fire during the bake.

## Running it locally

```bash
cd app && npm install && npm start          # http://localhost:8080/health
docker build -t fargate-web . && docker run -p 8080:8080 fargate-web
```

## What the app demonstrates

- `/health` — liveness, no dependencies, used by the ALB target group
- `/ready` — readiness, checks dependencies; returns 503 while warming up
- `/version` — the image tag and git SHA baked in at build time, so you can see which fleet
  answered during a traffic shift
- `/metrics` — Prometheus-format counters, and EMF-formatted log lines for CloudWatch metrics
  without an agent
- SIGTERM handling with connection draining, matched to the target group's deregistration delay

## Cost

NAT gateway (~$32/month) and ALB (~$16/month) dominate; two 0.25 vCPU Fargate tasks are ~$18/month.
Set `use_fargate_spot = true` to cut task cost ~70% (fine for stateless web tiers that tolerate a
two-minute interruption notice), or `assign_public_ip = true` with `enable_nat_gateway = false` to
drop NAT in dev.

## Teardown

```bash
cd infra && terraform destroy
```
