resource "random_id" "artifacts" {
  byte_length = 3
}

resource "aws_s3_bucket" "artifacts" {
  count         = var.enable_pipeline ? 1 : 0
  bucket        = "${local.name}-artifacts-${random_id.artifacts.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  count                   = var.enable_pipeline ? 1 : 0
  bucket                  = aws_s3_bucket.artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  count  = var.enable_pipeline ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  count  = var.enable_pipeline ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  count  = var.enable_pipeline ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id

  rule {
    id     = "expire-artifacts"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

# ------------------------------------------------------------------ codebuild
resource "aws_codebuild_project" "this" {
  for_each = var.enable_pipeline ? toset(local.services) : toset([])

  name          = "${local.name}-${each.value}"
  description   = "Builds and pushes the ${each.value} image"
  service_role  = aws_iam_role.codebuild[0].arn
  build_timeout = 20

  artifacts {
    type = "CODEPIPELINE"
  }

  environment {
    compute_type = "BUILD_GENERAL1_SMALL"
    image        = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type         = "LINUX_CONTAINER"
    # Required to run docker build inside CodeBuild.
    privileged_mode = true

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = data.aws_region.current.name
    }

    environment_variable {
      name  = "REPOSITORY_URI"
      value = aws_ecr_repository.this[each.value].repository_url
    }

    environment_variable {
      name  = "SERVICE_NAME"
      value = each.value
    }

    environment_variable {
      name  = "CONTAINER_NAME"
      value = each.value
    }
  }

  source {
    type      = "CODEPIPELINE"
    buildspec = "buildspecs/${each.value}.yml"
  }

  logs_config {
    cloudwatch_logs {
      group_name  = "/aws/codebuild/${local.name}-${each.value}"
      stream_name = "build"
    }
  }
}

# ---------------------------------------------------------------- pipeline
resource "aws_codepipeline" "this" {
  count = var.enable_pipeline ? 1 : 0

  name     = "${local.name}-pipeline"
  role_arn = aws_iam_role.codepipeline[0].arn

  artifact_store {
    location = aws_s3_bucket.artifacts[0].bucket
    type     = "S3"
  }

  stage {
    name = "Source"

    action {
      name             = "Source"
      category         = "Source"
      owner            = "AWS"
      provider         = "CodeStarSourceConnection"
      version          = "1"
      output_artifacts = ["source"]

      configuration = {
        ConnectionArn        = var.codestar_connection_arn
        FullRepositoryId     = var.github_repository
        BranchName           = var.github_branch
        DetectChanges        = "true"
        OutputArtifactFormat = "CODE_ZIP"
      }
    }
  }

  # Both images build in parallel — same run_order.
  stage {
    name = "Build"

    action {
      name             = "BuildApi"
      category         = "Build"
      owner            = "AWS"
      provider         = "CodeBuild"
      version          = "1"
      run_order        = 1
      input_artifacts  = ["source"]
      output_artifacts = ["api_build"]

      configuration = {
        ProjectName = aws_codebuild_project.this["api"].name
      }
    }

    action {
      name             = "BuildWeb"
      category         = "Build"
      owner            = "AWS"
      provider         = "CodeBuild"
      version          = "1"
      run_order        = 1
      input_artifacts  = ["source"]
      output_artifacts = ["web_build"]

      configuration = {
        ProjectName = aws_codebuild_project.this["web"].name
      }
    }
  }

  stage {
    name = "Deploy"

    action {
      name            = "DeployApi"
      category        = "Deploy"
      owner           = "AWS"
      provider        = "ECS"
      version         = "1"
      run_order       = 1
      input_artifacts = ["api_build"]

      configuration = {
        ClusterName = aws_ecs_cluster.this.name
        ServiceName = aws_ecs_service.api.name
        FileName    = "imagedefinitions.json"
        # Wait for the rollout; the circuit breaker fails the action on rollback.
        DeploymentTimeout = "15"
      }
    }

    action {
      name            = "DeployWeb"
      category        = "Deploy"
      owner           = "AWS"
      provider        = "ECS"
      version         = "1"
      run_order       = 1
      input_artifacts = ["web_build"]

      configuration = {
        ClusterName       = aws_ecs_cluster.this.name
        ServiceName       = aws_ecs_service.web.name
        FileName          = "imagedefinitions.json"
        DeploymentTimeout = "15"
      }
    }
  }
}

# Pipeline failures should page someone; a silent red pipeline is how a team
# ships nothing for two days without noticing.
resource "aws_cloudwatch_event_rule" "pipeline_failed" {
  count       = var.enable_pipeline ? 1 : 0
  name        = "${local.name}-pipeline-failed"
  description = "CodePipeline execution failed"

  event_pattern = jsonencode({
    source = ["aws.codepipeline"]
    "detail-type" = ["CodePipeline Pipeline Execution State Change"]
    detail = {
      state    = ["FAILED"]
      pipeline = [aws_codepipeline.this[0].name]
    }
  })
}

resource "aws_cloudwatch_event_target" "pipeline_failed" {
  count     = var.enable_pipeline ? 1 : 0
  rule      = aws_cloudwatch_event_rule.pipeline_failed[0].name
  target_id = "sns"
  arn       = aws_sns_topic.alerts.arn
}
