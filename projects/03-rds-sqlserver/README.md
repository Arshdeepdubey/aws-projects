# 03 — Deploying SQL Server Databases on Amazon RDS

A SQL Server Express/Standard instance in private subnets, with the admin password generated and
stored in Secrets Manager, a bastion-free access path through SSM Session Manager port forwarding,
automated backups, and CloudWatch alarms on CPU, storage and connections.

```
VPC (2 AZs)
├── public subnets  ──> NAT gateway (optional), SSM endpoint path
├── private subnets ──> RDS SQL Server (encrypted, Multi-AZ optional)
└── app security group ──> RDS security group :1433
```

## What is in here

| Path | Purpose |
|------|---------|
| `infra/network.tf` | VPC, subnets, route tables, endpoints for SSM |
| `infra/rds.tf` | Subnet group, parameter group, option group, DB instance |
| `infra/security.tf` | Security groups, KMS key, Secrets Manager secret + rotation hook |
| `infra/monitoring.tf` | CloudWatch alarms and log exports |
| `scripts/connect.sh` | SSM port forward to localhost:1433 |
| `scripts/bootstrap.sql` | Creates the application database, login and least-privilege user |

## Edition and licensing

`engine = "sqlserver-ex"` (Express) is the default: no licence cost, 10 GB per database, no Multi-AZ.
For Standard/Web/Enterprise switch `engine` and pick a matching `instance_class` — licence cost is
baked into the hourly rate (licence-included model). `sqlserver-ex` ignores `multi_az = true`, so
the variable validation blocks that combination rather than letting the apply fail 20 minutes in.

## Prerequisites

- Terraform >= 1.6, AWS CLI v2 with the Session Manager plugin
- `sqlcmd` or Azure Data Studio / SSMS for the bootstrap script

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # choose engine, size, allowed CIDRs
terraform init && terraform apply              # ~15–25 minutes for a SQL Server instance
terraform output -raw secret_arn
```

## Connect

The instance is not publicly accessible. Port-forward through SSM:

```bash
../scripts/connect.sh          # forwards 127.0.0.1:1433 -> RDS:1433

# in another shell
aws secretsmanager get-secret-value --secret-id "$(terraform -chdir=infra output -raw secret_name)" \
  --query SecretString --output text | jq -r .password

sqlcmd -S 127.0.0.1,1433 -U admin -P '<password>' -i ../scripts/bootstrap.sql
```

Setting `publicly_accessible = true` plus your IP in `allowed_cidr_blocks` is supported for quick
experiments but the default is private.

## Backups and restore

`backup_retention_period` defaults to 7 days with a daily window, and point-in-time restore is
enabled. Native backup/restore to S3 is wired through the option group (`SQLSERVER_BACKUP_RESTORE`)
so you can `RESTORE DATABASE ... FROM URL` from the bucket Terraform creates:

```sql
EXEC msdb.dbo.rds_restore_database
  @restore_db_name = 'AppDb',
  @s3_arn_to_restore = 'arn:aws:s3:::<bucket>/AppDb.bak';
```

## Cost

`db.t3.small` Express, single-AZ, 20 GB gp3: roughly $25–30/month plus storage. Standard edition
roughly triples it. Multi-AZ doubles the instance cost. **Destroy when you are done experimenting.**

## Teardown

```bash
cd infra && terraform destroy
```

`skip_final_snapshot` defaults to true in dev; set it false for anything you care about.
