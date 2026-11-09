data "aws_caller_identity" "current" {}

# The OIDC provider stays in Terraform: it is shared account infrastructure, not
# the deploy identity. The role that assumes it is deliberately NOT managed here
# - see infra/bootstrap/README.md for why and how it is created.
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # GitHub's OIDC root CA thumbprint. AWS validates the provider certificate
  # chain and this value is retained for IAM provider compatibility.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}
