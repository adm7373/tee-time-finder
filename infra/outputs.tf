output "cognito_region" {
  description = "Region containing the Cognito user pool."
  value       = var.aws_region
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID for application configuration."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  description = "Public Cognito app client ID for application configuration."
  value       = aws_cognito_user_pool_client.web.id
}

output "github_actions_role_arn" {
  description = "IAM role ARN for the GitHub Actions AWS_ROLE_ARN secret."
  value       = aws_iam_role.github_actions_terraform.arn
}


output "api_url" {
  description = "HTTPS URL for the HTTP API."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

