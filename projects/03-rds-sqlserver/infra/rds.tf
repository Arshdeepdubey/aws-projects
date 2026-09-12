resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${local.name}-subnets" }
}

resource "aws_db_parameter_group" "this" {
  name        = "${local.name}-params"
  family      = "${var.engine}-16.0"
  description = "${local.name} SQL Server parameters"

  parameter {
    name         = "rds configure ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# S3 bucket used by the native backup/restore option.
resource "aws_s3_bucket" "backups" {
  bucket        = "${local.name}-backups-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "rds_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${local.name}-rds-backup"
  assume_role_policy = data.aws_iam_policy_document.rds_assume.json
}

data "aws_iam_policy_document" "backup" {
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.backups.arn]
  }

  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.backups.arn}/*"]
  }

  statement {
    actions   = ["kms:DescribeKey", "kms:GenerateDataKey", "kms:Decrypt", "kms:Encrypt"]
    resources = [aws_kms_key.db.arn]
  }
}

resource "aws_iam_role_policy" "backup" {
  name   = "${local.name}-rds-backup"
  role   = aws_iam_role.backup.id
  policy = data.aws_iam_policy_document.backup.json
}

resource "aws_db_option_group" "this" {
  name                     = "${local.name}-options"
  engine_name              = var.engine
  major_engine_version     = "16.00"
  option_group_description = "${local.name} native backup/restore"

  option {
    option_name = "SQLSERVER_BACKUP_RESTORE"

    option_settings {
      name  = "IAM_ROLE_ARN"
      value = aws_iam_role.backup.arn
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  identifier = local.name

  engine         = var.engine
  engine_version = var.engine_version
  license_model  = "license-included"
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage > 0 ? var.max_allocated_storage : null
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.db.arn

  username = var.master_username
  password = random_password.master.result
  port     = 1433

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = var.publicly_accessible
  multi_az               = var.multi_az
  parameter_group_name   = aws_db_parameter_group.this.name
  option_group_name      = aws_db_option_group.this.name

  backup_retention_period    = var.backup_retention_period
  backup_window              = "18:00-19:00" # UTC
  maintenance_window         = "Sun:19:30-Sun:20:30"
  copy_tags_to_snapshot      = true
  auto_minor_version_upgrade = true

  enabled_cloudwatch_logs_exports = ["error", "agent"]
  performance_insights_enabled    = var.instance_class != "db.t3.micro"
  monitoring_interval             = 60
  monitoring_role_arn             = aws_iam_role.enhanced_monitoring.arn

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${local.name}-final-${formatdate("YYYYMMDDhhmm", timestamp())}"
  apply_immediately         = var.environment != "prod"

  timeouts {
    create = "60m"
    update = "80m"
    delete = "60m"
  }

  lifecycle {
    ignore_changes = [final_snapshot_identifier]

    precondition {
      condition     = !(var.engine == "sqlserver-ex" && var.multi_az)
      error_message = "SQL Server Express does not support Multi-AZ. Use sqlserver-se or set multi_az = false."
    }
  }
}

data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "enhanced_monitoring" {
  name               = "${local.name}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json
}

resource "aws_iam_role_policy_attachment" "enhanced_monitoring" {
  role       = aws_iam_role.enhanced_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
