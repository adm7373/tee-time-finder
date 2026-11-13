resource "aws_dynamodb_table" "courses" {
  name         = "${var.project_name}-courses"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

data "aws_iam_policy_document" "lambda_courses" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.courses.arn]
  }
}

resource "aws_iam_role_policy" "lambda_courses" {
  name   = "${var.project_name}-courses"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_courses.json
}
