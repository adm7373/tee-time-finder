# Deploy role bootstrap

The IAM role the GitHub Actions workflow assumes is **not managed by Terraform**.
It is created once, by hand, from the policy in this directory, and then left
alone. Everything else in `infra/` is deployed by the workflow itself.

## Why

A role that can widen its own permissions is a privilege-escalation path: anyone
who can push to `main` would effectively get admin. It also cannot grant itself
a permission it does not already have, so a change like "the deploy now needs
`dynamodb:CreateTable`" fails inside the very pipeline that was supposed to make
it. Keeping the role outside Terraform makes that a deliberate, auditable step
instead of a side effect of a merge.

## What it is

| | |
|---|---|
| Role name | `tee-time-finder-github-actions-terraform` |
| Trust | GitHub OIDC, restricted to `main` and the `production` environment |
| Permissions | `github-actions-policy.json` in this directory |
| Assumed by | `AWS_ROLE_ARN` repository secret |

No long-lived access keys are involved.

## Creating it

The OIDC provider is created by Terraform (`infra/github_actions.tf`) and is
shared. If you ever need to recreate the role from scratch:

```bash
ACCOUNT=550968370453
ROLE=tee-time-finder-github-actions-terraform

# Trust policy: only this repo, only main and the production environment.
cat > /tmp/trust.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
      },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": [
          "repo:adm7373@12490173/tee-time-finder@1443696246:ref:refs/heads/main",
          "repo:adm7373@12490173/tee-time-finder@1443696246:environment:production"
        ]
      }
    }
  }]
}
JSON

aws iam create-role --role-name "$ROLE" --assume-role-policy-document file:///tmp/trust.json

aws iam put-role-policy \
  --role-name "$ROLE" \
  --policy-name tee-time-finder-terraform-resources \
  --policy-document file://github-actions-policy.json
```

The resulting ARN goes into the repository secret:

```bash
gh secret set AWS_ROLE_ARN --body "arn:aws:iam::${ACCOUNT}:role/${ROLE}"
```

## Changing the permissions

This is the step that no longer happens by itself. When Terraform needs a new
permission, edit `github-actions-policy.json`, then:

```bash
aws iam put-role-policy \
  --role-name tee-time-finder-github-actions-terraform \
  --policy-name tee-time-finder-terraform-resources \
  --policy-document file://infra/bootstrap/github-actions-policy.json
```

Then re-run the workflow. IAM changes take effect within seconds, but a token
minted before the change keeps its old permissions until it expires.

## Removing it from Terraform (one-off)

The role was previously managed in `infra/github_actions.tf`. It has been
removed from the configuration, so the next apply would otherwise **destroy**
it and break the pipeline. Run this once, with credentials that can read and
write the state bucket, from `infra/`:

```bash
terraform init -backend-config="bucket=tee-time-finder-terraform-state-550968370453" \
               -backend-config="region=us-east-1"

terraform state rm aws_iam_role.github_actions_terraform
terraform state rm aws_iam_role_policy.github_actions_terraform
```

The role itself stays in AWS, untouched; only Terraform's record of it goes.

## Auditing

```bash
aws iam get-role --role-name tee-time-finder-github-actions-terraform
aws iam get-role-policy --role-name tee-time-finder-github-actions-terraform \
  --policy-name tee-time-finder-terraform-resources
```
