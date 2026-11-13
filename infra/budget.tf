# Cost guardrail.
#
# AWS has no spend ceiling: free-tier limits are a billing credit, not a switch
# that turns services off. Exceeding them bills at the normal rate, quietly.
# This budget is the thing that says so out loud, and the action is the only
# part that can actually stop spending.
#
# Two thresholds, deliberately different:
#
#   $5  — notify. The realistic monthly spend for this project is well under a
#         dollar, so $5 means something is wrong. An email, not a shutdown.
#   $20 — act. An automatic budget action attaches a deny policy to the Lambda
#         execution role, which stops the two scheduled workers. It does NOT
#         touch the API's Cognito sign-in, because that needs no AWS
#         permissions beyond DynamoDB reads, and a broken sign-in is a worse
#         outcome than a stopped nightly sweep.

data "aws_caller_identity" "budget" {}

locals {
  # Alerts go here. Set TF_VAR_alert_email, or pass -var alert_email=...;
  # leaving it empty creates the budget without notifications.
  budget_email = var.alert_email != "" ? [var.alert_email] : []
}

resource "aws_budgets_budget" "project" {
  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Scoped to this project's tag, not the whole account: with a whole-account
  # budget, any other experiment trips a $5 ceiling that was never about it.
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$${var.project_name}"]
  }

  dynamic "notification" {
    for_each = local.budget_email
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = 80
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [notification.value]
    }
  }

  dynamic "notification" {
    for_each = local.budget_email
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = 100
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [notification.value]
    }
  }
}

# --- the action -------------------------------------------------------------

data "aws_partition" "current" {}

data "aws_iam_policy_document" "budget_action_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["budgets.${data.aws_partition.current.dns_suffix}"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "budget_action" {
  name               = "${var.project_name}-budget-action"
  assume_role_policy = data.aws_iam_policy_document.budget_action_assume.json
}

# Budgets needs to attach the policy to the role, and to read the role back to
# revert the action when spend falls again (or when the month rolls over).
data "aws_iam_policy_document" "budget_action_permissions" {
  statement {
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetRole",
      "iam:ListAttachedRolePolicies",
    ]
    resources = [aws_iam_role.lambda_execution.arn]
  }
}

resource "aws_iam_role_policy" "budget_action" {
  name   = "${var.project_name}-budget-action"
  role   = aws_iam_role.budget_action.id
  policy = data.aws_iam_policy_document.budget_action_permissions.json
}

# Attached by the budget action at the threshold, and detached automatically
# when spend drops back under it. Deny wins over any Allow, so the workers stop
# on their next invocation rather than failing to deploy.
data "aws_iam_policy_document" "spend_stop" {
  statement {
    effect = "Deny"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.courses.arn,
      aws_dynamodb_table.geocode.arn,
    ]
  }
}

resource "aws_iam_policy" "spend_stop" {
  name        = "${var.project_name}-spend-stop"
  description = "Attached by the budget action when monthly spend passes the limit"
  policy      = data.aws_iam_policy_document.spend_stop.json
}

resource "aws_budgets_budget_action" "stop_workers" {
  budget_name       = aws_budgets_budget.project.name
  action_type       = "APPLY_IAM_POLICY"
  approval_model    = "AUTOMATIC"
  notification_type = "ACTUAL"

  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = var.budget_action_threshold_percent
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.spend_stop.arn
      roles      = [aws_iam_role.lambda_execution.name]
    }
  }

  dynamic "subscriber" {
    for_each = local.budget_email
    content {
      address           = subscriber.value
      subscription_type = "EMAIL"
    }
  }
}
