# Terraform infrastructure

This directory manages the initial AWS resources. It currently creates only a Cognito user pool and a public app client. It intentionally does not create a user or store any personal account information.

From this directory:

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

