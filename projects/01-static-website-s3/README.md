# 01 — Hosting a Static Website on Amazon S3

Private S3 bucket serving a static site through CloudFront with Origin Access Control, optional
custom domain with an ACM certificate and Route 53 alias records.

```
Route 53 ──> CloudFront (OAC, TLS) ──> S3 bucket (private, versioned, SSE-S3)
                   │
                   └─> access logs bucket
```

## Why not S3 website hosting

S3's static website endpoint is HTTP-only and needs a public bucket. CloudFront + OAC keeps the
bucket private, terminates TLS, and gives you caching, compression and custom error pages. The
only thing you lose is S3's directory-index behaviour, which the CloudFront Function in
`infra/functions/index-rewrite.js` restores.

## Prerequisites

- Terraform >= 1.6, AWS CLI v2
- For a custom domain: a Route 53 public hosted zone you control

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # set project, domain (optional)
terraform init
terraform plan
terraform apply

# upload the site
aws s3 sync ../site/ "s3://$(terraform output -raw bucket_name)/" --delete
aws cloudfront create-invalidation \
  --distribution-id "$(terraform output -raw distribution_id)" --paths '/*'

terraform output site_url
```

Or from the project root: `make deploy`.

## Custom domain

Set `domain_name` and `hosted_zone_id` in `terraform.tfvars`. The ACM certificate is created in
`us-east-1` (CloudFront requirement) via the aliased provider and validated with DNS records in
your hosted zone. Leave `domain_name` empty to use the default `*.cloudfront.net` name.

## Cost

Free tier covers a low-traffic site. Beyond it: S3 storage ~$0.023/GB-month, CloudFront ~$0.085/GB
out (first 1 TB), requests negligible. No hourly charges.

## Teardown

```bash
cd infra && terraform destroy
```

Buckets have `force_destroy = true` in non-production so `destroy` does not fail on objects.
