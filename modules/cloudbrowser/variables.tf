variable "name" {
  description = "Unique name prefix for all resources created by this module."
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy resources into."
  type        = string
  default     = "us-east-1"
}

# ── Bedrock ──────────────────────────────────────────────────────────────────

variable "bedrock_model_id" {
  description = "Amazon Bedrock model ID used to analyse logs and generate the report (e.g. 'anthropic.claude-3-5-sonnet-20241022-v2:0')."
  type        = string
}

# ── Notification ─────────────────────────────────────────────────────────────

variable "emails" {
  description = "One or more email addresses that will receive the SNS report."
  type        = list(string)

  validation {
    condition     = length(var.emails) > 0
    error_message = "At least one email address must be provided."
  }
}

# ── Resource Targeting ───────────────────────────────────────────────────────

variable "lambda_patterns" {
  description = "List of Lambda function name patterns to include. Supports shell-style wildcards (e.g. 'api-*', 'prod-*-handler'). Leave empty to skip Lambda scanning."
  type        = list(string)
  default     = []
}

variable "ecs_cluster_patterns" {
  description = "List of ECS cluster name patterns to include. Supports shell-style wildcards. Leave empty to skip ECS scanning."
  type        = list(string)
  default     = []
}

variable "step_function_patterns" {
  description = "List of Step Function state machine name patterns to include. Supports shell-style wildcards. Leave empty to skip Step Function scanning."
  type        = list(string)
  default     = []
}

variable "log_group_patterns" {
  description = "Additional CloudWatch log group name patterns to query directly (prefix match). Leave empty to rely solely on resource-derived log groups."
  type        = list(string)
  default     = []
}

# ── Log Filtering ────────────────────────────────────────────────────────────

variable "log_levels" {
  description = "Log levels to include in the scan. Valid values: ALL, ERROR, WARN, INFO, DEBUG. Use ['ALL'] to capture every level."
  type        = list(string)
  default     = ["ERROR", "WARN"]

  validation {
    condition = alltrue([
      for lvl in var.log_levels : contains(["ALL", "ERROR", "WARN", "INFO", "DEBUG"], upper(lvl))
    ])
    error_message = "Each log level must be one of: ALL, ERROR, WARN, INFO, DEBUG."
  }
}

variable "lookback_hours" {
  description = "Number of hours of logs to look back on each run."
  type        = number
  default     = 24

  validation {
    condition     = var.lookback_hours > 0 && var.lookback_hours <= 720
    error_message = "lookback_hours must be between 1 and 720."
  }
}

variable "max_log_events_per_group" {
  description = "Maximum number of log events to retrieve per log group per run (to control token usage)."
  type        = number
  default     = 500
}

# ── Schedule ─────────────────────────────────────────────────────────────────

variable "schedule_expression" {
  description = "EventBridge Scheduler cron or rate expression (e.g. 'cron(0 8 * * ? *)' for 08:00 UTC daily, or 'rate(1 day)')."
  type        = string
  default     = "cron(0 8 * * ? *)"
}

variable "schedule_timezone" {
  description = "IANA timezone for the EventBridge schedule (e.g. 'America/Chicago')."
  type        = string
  default     = "UTC"
}

# ── Lambda Runtime ───────────────────────────────────────────────────────────

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds. Large environments may need more time."
  type        = number
  default     = 300
}

variable "lambda_memory_mb" {
  description = "Lambda function memory in MB."
  type        = number
  default     = 512
}

# ── GitHub Integration ────────────────────────────────────────────────────────

variable "github_repo" {
  description = "GitHub repository in 'owner/repo' format (e.g. 'acme/my-app'). When set together with github_pat_secret_arn, CloudBrowser will open a PR against this repo whenever issues are detected."
  type        = string
  default     = ""
}

variable "github_pat_secret_arn" {
  description = "ARN of the AWS Secrets Manager secret that holds the GitHub Personal Access Token. The secret value may be a plain-text token string or a JSON object with a single key whose value is the token (e.g. {\"token\": \"ghp_...\"}). The token must have 'repo' scope (contents + pull_requests write)."
  type        = string
  default     = ""
}

variable "github_pr_health_threshold" {
  description = "Minimum health score (0-100) below which a GitHub PR is automatically opened. A PR is also opened whenever a HIGH-priority recommendation exists, regardless of this threshold."
  type        = number
  default     = 80

  validation {
    condition     = var.github_pr_health_threshold >= 0 && var.github_pr_health_threshold <= 100
    error_message = "github_pr_health_threshold must be between 0 and 100."
  }
}

# ── Tags ─────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Map of additional tags applied to all resources."
  type        = map(string)
  default     = {}
}
