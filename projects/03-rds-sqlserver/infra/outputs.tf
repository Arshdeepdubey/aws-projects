output "db_instance_id" {
  value = aws_db_instance.this.id
}

output "db_endpoint" {
  description = "host:port of the instance."
  value       = aws_db_instance.this.endpoint
}

output "db_address" {
  value = aws_db_instance.this.address
}

output "secret_name" {
  description = "Secrets Manager secret holding the master credentials."
  value       = aws_secretsmanager_secret.db.name
}

output "secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}

output "app_security_group_id" {
  description = "Attach this SG to application compute to allow database access."
  value       = aws_security_group.app.id
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.id
}
