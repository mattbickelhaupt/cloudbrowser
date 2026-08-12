resource "aws_scheduler_schedule" "cloudbrowser" {
  name                         = "${var.name}-cloudbrowser"
  description                  = "Triggers CloudBrowser log analysis for ${var.name}"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  state                        = "ENABLED"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 30
  }

  target {
    arn      = aws_lambda_function.cloudbrowser.arn
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      source = "eventbridge-scheduler"
      name   = var.name
    })

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}
