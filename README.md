# CloudBrowser

> AI-powered CloudWatch log analysis and reporting, packaged as a reusable Terraform module.

CloudBrowser deploys a Lambda function that periodically scans your Lambda functions, ECS tasks,
Step Functions, and CloudWatch log groups, passes the logs and metrics through an Amazon Bedrock
foundation model of your choice, and delivers a human-readable + machine-readable report to one
or more email addresses via SNS.

---

## Architecture

```
EventBridge Scheduler
       │  (cron)
       ▼
  Lambda Function
  ┌─────────────────────────────────────────┐
  │ 1. Discover matching log groups         │
  │    (Lambda / ECS / Step Functions /     │
  │     explicit patterns)                  │
  │ 2. Fetch filtered log events            │
  │ 3. Collect CloudWatch metrics           │
  │ 4. Invoke Bedrock → generate report     │
  │ 5. Publish report to SNS                │
  └─────────────────────────────────────────┘
       │
       ▼
  SNS Topic ──► Email(s)
```

---

## Quick Start

```hcl
module "cloudbrowser" {
  source = "github.com/your-org/cloudbrowser//modules/cloudbrowser"

  name             = "my-project"
  aws_region       = "us-east-1"
  bedrock_model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"

  emails = [
    "platform-team@example.com",
    "on-call@example.com",
  ]

  # Scan all Lambda functions whose names start with "api-" or "worker-"
  lambda_patterns = ["api-*", "worker-*"]

  # Scan ECS clusters named "production" or anything matching "prod-*"
  ecs_cluster_patterns = ["production", "prod-*"]

  # Scan specific Step Function state machines
  step_function_patterns = ["order-processing", "data-pipeline-*"]

  # Also scan these log groups directly
  log_group_patterns = ["/app/nginx/*", "/custom/audit-log"]

  # Only surface errors and warnings
  log_levels = ["ERROR", "WARN"]

  # Look back 24 hours on each run (default)
  lookback_hours = 24

  # Run every day at 08:00 UTC (default)
  schedule_expression = "cron(0 8 * * ? *)"
  schedule_timezone   = "America/Chicago"

  tags = {
    Environment = "production"
    Team        = "platform"
  }
}
```

> **Note:** SNS email subscriptions require confirmation. Each recipient will receive a
> "Confirm subscription" email from AWS after the first `terraform apply`.

---

## Input Variables

| Variable | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | yes | — | Unique name prefix for all resources |
| `aws_region` | `string` | no | `"us-east-1"` | AWS region to deploy into |
| `bedrock_model_id` | `string` | yes | — | Bedrock model ID (see [Supported Models](#supported-models)) |
| `emails` | `list(string)` | yes | — | Email addresses to receive reports |
| `lambda_patterns` | `list(string)` | no | `[]` | Lambda function name glob patterns |
| `ecs_cluster_patterns` | `list(string)` | no | `[]` | ECS cluster name glob patterns |
| `step_function_patterns` | `list(string)` | no | `[]` | Step Function state machine name glob patterns |
| `log_group_patterns` | `list(string)` | no | `[]` | Extra CloudWatch log group prefix/glob patterns |
| `log_levels` | `list(string)` | no | `["ERROR","WARN"]` | Log levels to include: `ALL`, `ERROR`, `WARN`, `INFO`, `DEBUG` |
| `lookback_hours` | `number` | no | `24` | Hours of logs to scan per run (1–720) |
| `max_log_events_per_group` | `number` | no | `500` | Max log events fetched per log group |
| `schedule_expression` | `string` | no | `"cron(0 8 * * ? *)"` | EventBridge cron or rate expression |
| `schedule_timezone` | `string` | no | `"UTC"` | IANA timezone for the schedule |
| `lambda_timeout` | `number` | no | `300` | Lambda timeout in seconds |
| `lambda_memory_mb` | `number` | no | `512` | Lambda memory in MB |
| `tags` | `map(string)` | no | `{}` | Additional tags applied to all resources |

---

## Outputs

| Output | Description |
|---|---|
| `lambda_function_arn` | ARN of the CloudBrowser Lambda function |
| `lambda_function_name` | Name of the CloudBrowser Lambda function |
| `sns_topic_arn` | ARN of the SNS report topic |
| `sns_topic_name` | Name of the SNS report topic |
| `scheduler_arn` | ARN of the EventBridge Scheduler |
| `lambda_role_arn` | ARN of the Lambda IAM execution role |

---

## Supported Models

Any Amazon Bedrock foundation model can be used. The handler includes first-class support for:

| Model Family | Example Model ID |
|---|---|
| Anthropic Claude | `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| Anthropic Claude | `anthropic.claude-3-haiku-20240307-v1:0` |
| Meta Llama 3 | `meta.llama3-70b-instruct-v1:0` |
| Amazon Titan | `amazon.titan-text-premier-v1:0` |

Ensure the model is **enabled in your AWS account** via the Bedrock console before deploying.

---

## Schedule Examples

```hcl
# Every day at 08:00 UTC (default)
schedule_expression = "cron(0 8 * * ? *)"

# Every 6 hours
schedule_expression = "rate(6 hours)"

# Every Monday at 09:00 in US Central time
schedule_expression = "cron(0 9 ? * MON *)"
schedule_timezone   = "America/Chicago"

# First day of every month at midnight UTC
schedule_expression = "cron(0 0 1 * ? *)"
```

---

## Report Format

Each run publishes one SNS message containing:

**Human Report** — Markdown-formatted summary including:
- Key findings and error patterns
- Per-service health assessment
- Actionable recommendations

**Machine Report** — JSON payload including:
```json
{
  "generated_at": "2026-08-11T08:00:00Z",
  "observation_window_hours": 24,
  "log_groups_scanned": 5,
  "total_events_analysed": 1234,
  "error_count": 12,
  "warn_count": 47,
  "top_errors": [
    { "message": "Connection refused", "count": 8, "log_group": "/aws/lambda/api-handler" }
  ],
  "recommendations": [
    { "priority": "HIGH", "description": "Investigate connection pool exhaustion in api-handler" }
  ],
  "health_score": 74
}
```

---

## IAM Permissions

The module creates an IAM role with the minimum permissions needed:

- `logs:*` — describe and query CloudWatch log groups
- `cloudwatch:GetMetricStatistics` — read Lambda/ECS/SFN metrics
- `lambda:ListFunctions` — discover Lambda functions
- `ecs:List*`, `ecs:Describe*` — discover ECS clusters and services
- `states:List*`, `states:Describe*` — discover Step Functions
- `bedrock:InvokeModel` — invoke the configured foundation model
- `sns:Publish` — publish reports to the SNS topic

---

## Requirements

| Name | Version |
|---|---|
| Terraform | >= 1.5 |
| AWS Provider | >= 5.30 |
| Archive Provider | >= 2.0 |
| Python (Lambda runtime) | 3.12 |
