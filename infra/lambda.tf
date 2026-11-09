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
      # The hosted-UI domain is where /oauth2/token lives; the API endpoint is
      # the app itself. These are different hosts and must not be conflated.
      BASE_URL             = aws_apigatewayv2_api.http.api_endpoint
      COGNITO_DOMAIN_URL   = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
      COGNITO_REDIRECT_URI = local.cognito_callback_url
    }
  }
}


resource "aws_apigatewayv2_api" "http" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
}

locals {
  # Single source of truth for the OAuth redirect URI: used for the app client's
  # callback_urls and handed to the Lambda so the browser never has to guess it.
  cognito_callback_url = "${trimsuffix(aws_apigatewayv2_api.http.api_endpoint, "/")}/"
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.http.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito"

  jwt_configuration {
    # Deliberately empty. API Gateway's JWT authorizer matches `audience` against
    # the token's `aud` claim, but Cognito ACCESS tokens - which is what this app
    # sends - carry `client_id` and no `aud` at all. Listing the app client here
    # therefore rejects every access token with a 401. The signature, issuer and
    # expiry are still enforced by API Gateway; the app client is verified from
    # `client_id` (access) or `aud` (ID) in app/auth.py::require_user.
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

resource "aws_apigatewayv2_route" "public_debug" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /debug"
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
