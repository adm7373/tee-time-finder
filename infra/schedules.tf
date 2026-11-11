# Both workers run overnight, fingerprint after discovery so the rows it reads
# exist. Times are UTC; America/New_York is UTC-4/5, so 10:00/10:30 UTC is
# ~5-6am local — before anyone searches and after Nominatim's quiet hours.
resource "aws_cloudwatch_event_rule" "discover" {
  name                = "${var.project_name}-discover"
  description         = "Nightly course discovery sweep"
  schedule_expression = "cron(0 10 * * ? *)"
}

resource "aws_cloudwatch_event_target" "discover" {
  rule = aws_cloudwatch_event_rule.discover.name
  arn  = aws_lambda_function.discover.arn
}

resource "aws_lambda_permission" "discover_schedule" {
  statement_id  = "AllowScheduledDiscover"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.discover.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.discover.arn
}

resource "aws_cloudwatch_event_rule" "fingerprint" {
  name                = "${var.project_name}-fingerprint"
  description         = "Nightly booking-page fingerprint sweep"
  schedule_expression = "cron(30 10 * * ? *)"
}

resource "aws_cloudwatch_event_target" "fingerprint" {
  rule = aws_cloudwatch_event_rule.fingerprint.name
  arn  = aws_lambda_function.fingerprint.arn
}

resource "aws_lambda_permission" "fingerprint_schedule" {
  statement_id  = "AllowScheduledFingerprint"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fingerprint.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.fingerprint.arn
}
