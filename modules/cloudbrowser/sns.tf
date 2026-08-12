resource "aws_sns_topic" "report" {
  name         = "${var.name}-cloudbrowser-report"
  display_name = "CloudBrowser Report - ${var.name}"

  tags = merge(var.tags, { Name = "${var.name}-cloudbrowser-report" })
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.emails)

  topic_arn = aws_sns_topic.report.arn
  protocol  = "email"
  endpoint  = each.value
}
