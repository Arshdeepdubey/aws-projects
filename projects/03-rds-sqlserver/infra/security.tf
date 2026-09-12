resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "Application tier; attach this to anything that needs the database"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-app" }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "RDS SQL Server"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "SQL Server from the application security group"
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = 1433
  to_port                      = 1433
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_cidr" {
  for_each = var.publicly_accessible ? toset(var.allowed_cidr_blocks) : toset([])

  security_group_id = aws_security_group.db.id
  description       = "SQL Server from an explicitly allowed CIDR"
  cidr_ipv4         = each.value
  from_port         = 1433
  to_port           = 1433
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "db_all" {
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------- encryption
resource "aws_kms_key" "db" {
  description             = "${local.name} RDS encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "db" {
  name          = "alias/${local.name}-rds"
  target_key_id = aws_kms_key.db.key_id
}

# ---------------------------------------------------------------- credentials
resource "random_password" "master" {
  length  = 32
  special = true
  # SQL Server rejects these in the master password.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "db" {
  name                    = "${var.project}/${var.environment}/rds-sqlserver"
  description             = "Master credentials for ${local.name}"
  kms_key_id              = aws_kms_key.db.arn
  recovery_window_in_days = 0 # dev convenience; raise to 7–30 in production
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id

  secret_string = jsonencode({
    engine   = "sqlserver"
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    username = var.master_username
    password = random_password.master.result
    dbname   = "master"
  })
}
