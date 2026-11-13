# Terraform infrastructure

This directory manages the AWS resources: a Cognito user pool and app client,
the API Lambda and its HTTP API, two DynamoDB tables (cached coordinates, and
discovered courses), the nightly discovery and fingerprint workers, and the
cost budget that guards the lot. It intentionally does not create a user or
store any personal account information.

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
- Optional repository variable `ALERT_EMAIL`, where the cost budget sends its notifications
- A protected GitHub `production` environment if manual approval is desired before `apply`

The IAM role needs only the permissions required by the Terraform resources and the S3 state bucket. The state bucket should have versioning, encryption, public access blocked, and restricted access for this role.

That role is **not managed by Terraform**. It is created once by hand from
`bootstrap/github-actions-policy.json`; see `bootstrap/README.md` for the
reasoning, the creation commands, and how to change its permissions.

## Cost budget

`budget.tf` is the guardrail against AWS's lack of a spend ceiling: going over
the free tier bills at normal rates rather than switching services off. It
creates a monthly budget scoped to this project's tag, not the whole account,
with two thresholds that do different things:

| | At | Effect |
|---|---|---|
| Notify | 100% of `monthly_budget_usd` (default $5) | Email to `ALERT_EMAIL` |
| Act | `budget_action_threshold_percent` (default 400%, so $20) | Deny policy attached to the Lambda execution role; the two scheduled workers stop |

Budgets detaches the policy again when spend falls back under the threshold or
the month rolls over. The API keeps working throughout: Cognito sign-in needs
no AWS permissions, and the deny policy only covers the two DynamoDB tables,
so the failure mode is a stopped nightly sweep rather than a broken sign-in.

Set the address with:

```bash
gh variable set ALERT_EMAIL --body "you@example.com"
```

Unset, the budget and its action still work; only the emails are skipped.

## Admin group

Terraform creates the Cognito `admins` group. Membership is deliberately **not**
in Terraform: who is in a group is account state, not infrastructure, and
declaring a real user would make every apply own their attributes and password.

Add yourself once:

```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$(terraform output -raw cognito_user_pool_id)" \
  --username you@example.com \
  --group-name admins
```

Then sign out and back in. Cognito bakes `cognito:groups` into the token at
issue time, so an existing session will not carry the new claim.
