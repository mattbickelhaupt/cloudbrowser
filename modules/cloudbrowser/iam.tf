data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

# ── Lambda Execution Role ─────────────────────────────────────────────────────

resource "aws_iam_role" "lambda_exec" {
  name = "${var.name}-cloudbrowser-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, { Name = "${var.name}-cloudbrowser-lambda" })
}

# Basic Lambda logging
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_cloudbrowser" {
  name = "${var.name}-cloudbrowser-permissions"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch Logs — discover and query log groups
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:FilterLogEvents",
          "logs:GetLogEvents",
          "logs:StartQuery",
          "logs:StopQuery",
          "logs:GetQueryResults",
          "logs:DescribeQueries"
        ]
        Resource = "*"
      },
      # CloudWatch Metrics — read metrics for Lambda, ECS, SFN
      {
        Sid    = "CloudWatchMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics"
        ]
        Resource = "*"
      },
      # Lambda — list and describe functions for pattern matching
      {
        Sid      = "LambdaRead"
        Effect   = "Allow"
        Action   = ["lambda:ListFunctions", "lambda:GetFunction"]
        Resource = "*"
      },
      # ECS — list clusters, services, tasks
      {
        Sid    = "ECSRead"
        Effect = "Allow"
        Action = [
          "ecs:ListClusters",
          "ecs:ListServices",
          "ecs:ListTasks",
          "ecs:DescribeClusters",
          "ecs:DescribeServices",
          "ecs:DescribeTasks"
        ]
        Resource = "*"
      },
      # Step Functions — list and describe state machines + executions
      {
        Sid    = "StepFunctionsRead"
        Effect = "Allow"
        Action = [
          "states:ListStateMachines",
          "states:ListExecutions",
          "states:DescribeStateMachine",
          "states:DescribeExecution",
          "states:GetExecutionHistory"
        ]
        Resource = "*"
      },
      # Bedrock — invoke the chosen model
      {
        Sid      = "BedrockInvoke"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
      },
      # SNS — publish the report
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.report.arn
      },
      # AWS Marketplace — list existing subscriptions and subscribe to new products
      {
        Sid    = "MarketplaceSubscriptions"
        Effect = "Allow"
        Action = [
          "aws-marketplace:ListEntitlements",
          "aws-marketplace:GetEntitlement",
          "aws-marketplace:Subscribe",
          "aws-marketplace:Unsubscribe",
          "aws-marketplace:ViewSubscriptions"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── EventBridge Scheduler Role ────────────────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name = "${var.name}-cloudbrowser-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = local.account_id
        }
      }
    }]
  })

  tags = merge(var.tags, { Name = "${var.name}-cloudbrowser-scheduler" })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "${var.name}-cloudbrowser-scheduler-invoke"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.cloudbrowser.arn
    }]
  })
}
