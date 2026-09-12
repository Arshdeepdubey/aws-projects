locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Repo        = "aws-lambda-functions"
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_dynamodb_table" "bookings" {
  name         = "${local.name}-bookings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "bookingRef"

  attribute {
    name = "bookingRef"
    type = "S"
  }

  attribute {
    name = "appointmentDate"
    type = "S"
  }

  global_secondary_index {
    name            = "date-index"
    hash_key        = "appointmentDate"
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

data "archive_file" "fulfillment" {
  type        = "zip"
  source_dir  = "${path.module}/../src/fulfillment"
  output_path = "${path.module}/.build/fulfillment.zip"
  excludes    = ["__pycache__", ".pytest_cache"]
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fulfillment" {
  name               = "${local.name}-fulfillment"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.fulfillment.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "fulfillment" {
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.bookings.arn,
      "${aws_dynamodb_table.bookings.arn}/index/*",
    ]
  }
}

resource "aws_iam_role_policy" "fulfillment" {
  name   = "${local.name}-fulfillment"
  role   = aws_iam_role.fulfillment.id
  policy = data.aws_iam_policy_document.fulfillment.json
}

resource "aws_cloudwatch_log_group" "fulfillment" {
  name              = "/aws/lambda/${local.name}-fulfillment"
  retention_in_days = 14
}

resource "aws_lambda_function" "fulfillment" {
  function_name    = "${local.name}-fulfillment"
  role             = aws_iam_role.fulfillment.arn
  handler          = "app.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = 15
  memory_size      = 256
  filename         = data.archive_file.fulfillment.output_path
  source_code_hash = data.archive_file.fulfillment.output_base64sha256

  environment {
    variables = {
      BOOKINGS_TABLE = aws_dynamodb_table.bookings.name
      OPEN_TIME      = var.opening_hours.start
      CLOSE_TIME     = var.opening_hours.end
      LOG_LEVEL      = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.fulfillment]
}

resource "aws_lambda_permission" "lex" {
  statement_id  = "AllowLexInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fulfillment.function_name
  principal     = "lexv2.amazonaws.com"
  source_arn    = "arn:aws:lex:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:bot-alias/${aws_lexv2models_bot.this.id}/*"
}
