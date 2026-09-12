variable "project" {
  type    = string
  default = "mssql"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "engine" {
  type        = string
  description = "sqlserver-ex | sqlserver-web | sqlserver-se | sqlserver-ee"
  default     = "sqlserver-ex"

  validation {
    condition     = contains(["sqlserver-ex", "sqlserver-web", "sqlserver-se", "sqlserver-ee"], var.engine)
    error_message = "engine must be one of sqlserver-ex, sqlserver-web, sqlserver-se, sqlserver-ee."
  }
}

variable "engine_version" {
  type        = string
  description = "Major.minor version. List with: aws rds describe-db-engine-versions --engine sqlserver-ex."
  default     = "16.00.4165.4.v1"
}

variable "instance_class" {
  type    = string
  default = "db.t3.small"
}

variable "allocated_storage" {
  type        = number
  description = "GiB. SQL Server minimum is 20 for Express, 200 for SE/EE."
  default     = 20
}

variable "max_allocated_storage" {
  type        = number
  description = "Upper bound for storage autoscaling. Set to 0 to disable."
  default     = 100
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "publicly_accessible" {
  type        = bool
  description = "Keep false; use the SSM port-forward in scripts/connect.sh instead."
  default     = false
}

variable "allowed_cidr_blocks" {
  type        = list(string)
  description = "Extra CIDRs allowed on 1433. Only used when publicly_accessible is true."
  default     = []
}

variable "master_username" {
  type    = string
  default = "admin"

  validation {
    condition     = !contains(["sa", "public", "sysadmin"], lower(var.master_username))
    error_message = "master_username cannot be a reserved SQL Server name."
  }
}

variable "backup_retention_period" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "skip_final_snapshot" {
  type    = bool
  default = true
}

variable "alarm_email" {
  type        = string
  description = "Address subscribed to the CloudWatch alarm topic. Empty disables the subscription."
  default     = ""
}
