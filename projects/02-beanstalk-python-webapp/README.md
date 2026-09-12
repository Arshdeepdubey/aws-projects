# 02 — Deploying a Simple Python Web Application with AWS Elastic Beanstalk

A Flask app on Elastic Beanstalk (Python 3.11 on Amazon Linux 2023), load balanced across two
Availability Zones, with the application bundle uploaded from Terraform so a single `apply` gives
you a running URL.

```
ALB ──> EB environment (Auto Scaling group, 1–4 t3.micro) ──> Flask / gunicorn
                     │
                     └─> CloudWatch Logs (streamed), health reporting: enhanced
```

## Layout

```
app/                  Flask application — this folder is zipped and uploaded as the EB version
  application.py      EB looks for `application` by default; gunicorn entrypoint is set in Procfile
  requirements.txt
  Procfile
  .ebextensions/      EB config: proxy, static files, env defaults
infra/                Terraform: EB application, environment, IAM roles, S3 bundle
```

## Prerequisites

- Terraform >= 1.6, AWS CLI v2
- Python 3.11 locally if you want to run the app before deploying

## Run locally

```bash
cd app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
flask --app application run --debug     # http://127.0.0.1:5000
```

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
terraform output environment_url
```

Terraform zips `app/` (hashing the contents so a code change produces a new version label),
uploads it to the versions bucket, and points the environment at it. Re-running `apply` after an
edit deploys the new version.

### Using the EB CLI instead

If you prefer `eb deploy` for day-to-day pushes, keep Terraform for the environment and run:

```bash
cd app
eb init -p python-3.11 <app-name> --region ap-south-1
eb use <env-name>
eb deploy
```

## Configuration

Environment variables for the app go in `var.app_environment_variables` (a map) — they become EB
`aws:elasticbeanstalk:application:environment` settings. Secrets belong in Secrets Manager; the
instance role already has `secretsmanager:GetSecretValue` scoped to `${project}/*`.

## Cost

Elastic Beanstalk itself is free; you pay for the ALB (~$16/month) and EC2 instances (t3.micro
~$7.50/month each, free tier eligible for 12 months). Set `environment_type = "SingleInstance"`
in tfvars to drop the load balancer while experimenting.

## Teardown

```bash
cd infra && terraform destroy
```
