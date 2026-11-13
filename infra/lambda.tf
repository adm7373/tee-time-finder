data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../build/lambda"
  output_path = "${path.module}/../build/tee-time-finder.zip"
}

resource "aws_iam_role" "lambda_execution" {
  name = "${var.project_name}-lambda-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# The API's admin routes start the two workers. Scoped to exactly those two
# functions: nothing else in the API can invoke a Lambda, and the workers
# themselves get no invoke permission at all.
data "aws_iam_policy_document" "api_invoke_workers" {
  statement {
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.discover.arn, aws_lambda_function.fingerprint.arn]
  }
}

resource "aws_iam_role_policy" "api_invoke_workers" {
  name   = "${var.project_name}-invoke-workers"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.api_invoke_workers.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "api" {
  function_name    = var.project_name
  role             = aws_iam_role.lambda_execution.arn
  runtime          = "python3.12"
  handler          = "app.main.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 30
  memory_size      = 512

  environment {
    variables = {
      DEFAULT_SEARCH_ZIP    = "02169"
      COGNITO_REGION        = var.aws_region
      COGNITO_USER_POOL_ID  = aws_cognito_user_pool.main.id
      COGNITO_APP_CLIENT_ID = aws_cognito_user_pool_client.web.id
      # The hosted UI is a different host from the API: /oauth2/token lives
      # there, while the API endpoint is the app itself.
      BASE_URL             = aws_apigatewayv2_api.http.api_endpoint
      COGNITO_DOMAIN_URL   = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
      COGNITO_REDIRECT_URI = local.cognito_callback_url
      # Where cached ZIP and course coordinates live; see infra/geocode.tf.
      GEOCODE_TABLE_NAME = aws_dynamodb_table.geocode.name
      # Where discovered courses and booking fingerprints live; see courses.tf.
      COURSES_TABLE_NAME = aws_dynamodb_table.courses.name
      # Used by the admin routes to invoke the workers asynchronously. AWS_REGION
      # is reserved by the Lambda runtime and is already set there.
      PROJECT_NAME = var.project_name
    }
  }
}


# --- discovery and fingerprint workers ---------------------------------------
#
# These are deliberately NOT behind API Gateway. They run on a schedule (see
# schedules.tf), do slow outbound scraping, and need minutes rather than the
# API's 30-second budget. They share the Lambda execution role and the same
# deployment package as the API — the handler entry points differ.

resource "aws_lambda_function" "discover" {
  function_name    = "${var.project_name}-discover"
  role             = aws_iam_role.lambda_execution.arn
  runtime          = "python3.12"
  handler          = "app.discovery.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 600
  memory_size      = 512

  environment {
    variables = {
      DEFAULT_SEARCH_ZIP = "02169"
      DEFAULT_RADIUS     = "25"
      GEOCODE_TABLE_NAME = aws_dynamodb_table.geocode.name
      COURSES_TABLE_NAME = aws_dynamodb_table.courses.name
    }
  }
}

resource "aws_lambda_function" "fingerprint" {
  function_name    = "${var.project_name}-fingerprint"
  role             = aws_iam_role.lambda_execution.arn
  runtime          = "python3.12"
  handler          = "app.fingerprint_handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 600
  memory_size      = 512

  environment {
    variables = {
      COURSES_TABLE_NAME = aws_dynamodb_table.courses.name
    }
  }
}


resource "aws_apigatewayv2_api" "http" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
}

locals {
  # Single source of truth for the OAuth callback: it is both the app client's
  # registered callback_url and the redirect URI the Lambda sends to the token
  # endpoint. If these drift apart Cognito answers error=redirect_mismatch.
  cognito_callback_url = "${trimsuffix(aws_apigatewayv2_api.http.api_endpoint, "/")}/"
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito"

  jwt_configuration {
    # Deliberately empty. API Gateway matches `audience` against the token's
    # `aud` claim, but Cognito ACCESS tokens - the ones this app sends - carry
    # `client_id` and no `aud` at all. Listing the app client here would reject
    # every access token with a 401. Signature, issuer and expiry are still
    # enforced at the edge; the app client is checked in app/auth.py.
    audience = []
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "ANY /{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "public_root" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "public_health" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /health"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "public_config" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /config"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "public_auth_token" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "POST /auth/token"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# Admin routes are authenticated but not open to every user: the handler checks
# Cognito group membership itself, so API Gateway only needs to verify the
# token. Both routes sit behind the same JWT authorizer as /search.
resource "aws_apigatewayv2_route" "admin" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "ANY /admin/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}
