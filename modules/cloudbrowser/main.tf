terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.30"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0"
    }
  }
}

locals {
  lambda_src_dir  = "${path.module}/lambda"
  lambda_zip_path = "${path.module}/.build/cloudbrowser.zip"

  common_tags = merge(var.tags, {
    ManagedBy = "cloudbrowser-terraform-module"
    Module    = var.name
  })
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = local.lambda_src_dir
  output_path = local.lambda_zip_path
}

resource "aws_lambda_function" "cloudbrowser" {
  function_name    = "${var.name}-cloudbrowser"
  description      = "CloudBrowser: scans logs/metrics and generates AI-powered reports via Bedrock"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory_mb

  environment {
    variables = {
      BEDROCK_MODEL_ID             = var.bedrock_model_id
      AWS_REGION_TARGET            = var.aws_region
      SNS_TOPIC_ARN                = aws_sns_topic.report.arn
      LAMBDA_PATTERNS              = jsonencode(var.lambda_patterns)
      ECS_CLUSTER_PATTERNS         = jsonencode(var.ecs_cluster_patterns)
      STEP_FUNCTION_PATTERNS       = jsonencode(var.step_function_patterns)
      LOG_GROUP_PATTERNS           = jsonencode(var.log_group_patterns)
      LOG_LEVELS                   = jsonencode([for lvl in var.log_levels : upper(lvl)])
      LOOKBACK_HOURS               = tostring(var.lookback_hours)
      MAX_LOG_EVENTS_PER_GROUP     = tostring(var.max_log_events_per_group)
      MODULE_NAME                  = var.name
    }
  }

  tags = local.common_tags
}

# Allow EventBridge Scheduler to invoke the function directly (belt-and-suspenders
# alongside the IAM role policy on the scheduler role)
resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cloudbrowser.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.cloudbrowser.arn
}

# CloudWatch Log Group for the Lambda function itself
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.cloudbrowser.function_name}"
  retention_in_days = 30

  tags = local.common_tags
}
