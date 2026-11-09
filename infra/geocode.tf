resource "aws_dynamodb_table" "geocode" {
  name         = "${var.project_name}-geocode"
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

data "aws_iam_policy_document" "lambda_geocode" {
  statement {
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.geocode.arn]
  }
}

resource "aws_iam_role_policy" "lambda_geocode" {
  name   = "${var.project_name}-geocode"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_geocode.json
}
