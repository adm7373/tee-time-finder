data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # GitHub's OIDC root CA thumbprint. AWS validates the provider certificate
  # chain and this value is retained for IAM provider compatibility.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_actions_terraform" {
  name = "${var.project_name}-github-actions-terraform"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:adm7373/tee-time-finder:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_terraform" {
  name = "${var.project_name}-terraform-resources"
  role = aws_iam_role.github_actions_terraform.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformState"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketVersioning",
        ]
        Resource = [
          "arn:aws:s3:::tee-time-finder-terraform-state-550968370453",
          "arn:aws:s3:::tee-time-finder-terraform-state-550968370453/tee-time-finder/*",
        ]
      },
      {
        Sid    = "Cognito"
        Effect = "Allow"
        Action = [
          "cognito-idp:CreateUserPool",
          "cognito-idp:DeleteUserPool",
          "cognito-idp:DescribeUserPool",
          "cognito-idp:UpdateUserPool",
          "cognito-idp:CreateUserPoolClient",
          "cognito-idp:DeleteUserPoolClient",
          "cognito-idp:DescribeUserPoolClient",
          "cognito-idp:UpdateUserPoolClient",
          "cognito-idp:ListUserPools",
          "cognito-idp:ListUserPoolClients",
        ]
        Resource = "*"
      },
      {
        Sid      = "IamOidcRoleManagement"
        Effect   = "Allow"
        Action   = ["iam:CreateOpenIDConnectProvider", "iam:GetOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider", "iam:CreateRole", "iam:GetRole", "iam:DeleteRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy"]
        Resource = "*"
      },
    ]
  })
}

