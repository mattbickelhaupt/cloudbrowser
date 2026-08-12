output "lambda_function_arn" {
  description = "ARN of the CloudBrowser Lambda function."
  value       = aws_lambda_function.cloudbrowser.arn
}

output "lambda_function_name" {
  description = "Name of the CloudBrowser Lambda function."
  value       = aws_lambda_function.cloudbrowser.function_name
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic that delivers reports."
  value       = aws_sns_topic.report.arn
}

output "sns_topic_name" {
  description = "Name of the SNS topic that delivers reports."
  value       = aws_sns_topic.report.name
}

output "scheduler_arn" {
  description = "ARN of the EventBridge Scheduler rule."
  value       = aws_scheduler_schedule.cloudbrowser.arn
}

output "lambda_role_arn" {
  description = "ARN of the IAM role assumed by the Lambda function."
  value       = aws_iam_role.lambda_exec.arn
}
