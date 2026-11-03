# Terraform infrastructure

This directory manages the initial AWS resources. It currently creates only a Cognito user pool and a public app client. It intentionally does not create a user or store any personal account information.

For local use, from this directory:

```bash
terraform init
terraform plan
terraform apply
```

After applying, copy the output values into an untracked local `.env` file or inject them through the deployment environment:

```text
COGNITO_REGION=<cognito_region>
COGNITO_USER_POOL_ID=<cognito_user_pool_id>
COGNITO_APP_CLIENT_ID=<cognito_app_client_id>
```

Terraform state may contain cloud resource metadata and must be kept private. For a shared or production deployment, configure an encrypted remote state backend before applying from multiple environments.

## GitHub Actions deployment

The workflow in `.github/workflows/terraform.yml` uses GitHub OIDC to assume an AWS IAM role. It does not use long-lived AWS access keys. Configure these repository settings before enabling deployment:

- Repository variable `AWS_REGION`, such as `us-east-1`
- Repository variable `TF_STATE_BUCKET`, an existing private S3 bucket for Terraform state
- Repository secret `AWS_ROLE_ARN`, an IAM role trusted only by this repository and its intended branches/environments
- A protected GitHub `production` environment if manual approval is desired before `apply`

The IAM role needs only the permissions required by the Terraform resources and the S3 state bucket. The state bucket should have versioning, encryption, public access blocked, and restricted access for this role.

