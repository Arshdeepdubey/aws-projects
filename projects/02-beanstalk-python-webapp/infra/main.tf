locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repo        = "aws-lambda-functions"
  }
  app_dir = "${path.module}/../app"
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# Default VPC keeps the template self-contained; point at your own VPC/subnets
# by replacing these data sources with variables.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "random_id" "suffix" {
  byte_length = 3
}

# ---------------------------------------------------------------- app bundle
data "archive_file" "app" {
  type        = "zip"
  source_dir  = local.app_dir
  output_path = "${path.module}/.build/app.zip"
  excludes    = [".venv", "__pycache__", ".pytest_cache"]
}

resource "aws_s3_bucket" "versions" {
  bucket        = "${local.name}-versions-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "versions" {
  bucket                  = aws_s3_bucket.versions.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "app" {
  bucket = aws_s3_bucket.versions.id
  key    = "bundles/app-${data.archive_file.app.output_md5}.zip"
  source = data.archive_file.app.output_path
  etag   = data.archive_file.app.output_md5
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${local.name}-eb-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "web_tier" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier"
}

resource "aws_iam_role_policy_attachment" "worker_tier" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkWorkerTier"
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/*"]
  }
}

resource "aws_iam_role_policy" "app_secrets" {
  name   = "${local.name}-app-secrets"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.app_secrets.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-eb-instance"
  role = aws_iam_role.instance.name
}

data "aws_iam_policy_document" "service_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["elasticbeanstalk.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "service" {
  name               = "${local.name}-eb-service"
  assume_role_policy = data.aws_iam_policy_document.service_assume.json
}

resource "aws_iam_role_policy_attachment" "service_enhanced_health" {
  role       = aws_iam_role.service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSElasticBeanstalkEnhancedHealth"
}

resource "aws_iam_role_policy_attachment" "service_managed_updates" {
  role       = aws_iam_role.service.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkManagedUpdatesCustomerRolePolicy"
}

# ---------------------------------------------------------------- beanstalk
resource "aws_elastic_beanstalk_application" "this" {
  name        = local.name
  description = "Flask web application (${var.environment})"

  appversion_lifecycle {
    service_role          = aws_iam_role.service.arn
    max_count             = 20
    delete_source_from_s3 = true
  }
}

resource "aws_elastic_beanstalk_application_version" "this" {
  name        = "${local.name}-${substr(data.archive_file.app.output_md5, 0, 8)}"
  application = aws_elastic_beanstalk_application.this.name
  bucket      = aws_s3_bucket.versions.id
  key         = aws_s3_object.app.key
  description = "Deployed by Terraform"
}

resource "aws_elastic_beanstalk_environment" "this" {
  name                = "${local.name}-env"
  application         = aws_elastic_beanstalk_application.this.name
  solution_stack_name = var.solution_stack_name
  version_label       = aws_elastic_beanstalk_application_version.this.name
  tier                = "WebServer"

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "IamInstanceProfile"
    value     = aws_iam_instance_profile.instance.name
  }

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "InstanceType"
    value     = var.instance_type
  }

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "DisableIMDSv1"
    value     = "true"
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "EnvironmentType"
    value     = var.environment_type
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "ServiceRole"
    value     = aws_iam_role.service.arn
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "LoadBalancerType"
    value     = "application"
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "VPCId"
    value     = data.aws_vpc.default.id
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "Subnets"
    value     = join(",", data.aws_subnets.default.ids)
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "ELBSubnets"
    value     = join(",", data.aws_subnets.default.ids)
  }

  setting {
    namespace = "aws:autoscaling:asg"
    name      = "MinSize"
    value     = tostring(var.min_size)
  }

  setting {
    namespace = "aws:autoscaling:asg"
    name      = "MaxSize"
    value     = tostring(var.max_size)
  }

  setting {
    namespace = "aws:elasticbeanstalk:healthreporting:system"
    name      = "SystemType"
    value     = "enhanced"
  }

  setting {
    namespace = "aws:elasticbeanstalk:command"
    name      = "DeploymentPolicy"
    value     = "Rolling"
  }

  setting {
    namespace = "aws:elasticbeanstalk:command"
    name      = "BatchSizeType"
    value     = "Percentage"
  }

  setting {
    namespace = "aws:elasticbeanstalk:command"
    name      = "BatchSize"
    value     = "50"
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "APP_ENVIRONMENT"
    value     = var.environment
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "APP_RELEASE"
    value     = substr(data.archive_file.app.output_md5, 0, 8)
  }

  dynamic "setting" {
    for_each = var.app_environment_variables
    content {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = setting.key
      value     = setting.value
    }
  }
}
