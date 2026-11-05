resource "aws_secretsmanager_secret" "teeitup" {
  name        = "${var.project_name}/teeitup"
  description = "TeeItUp Golf booking credentials for Norwood Country Club"

  tags = {
    Project   = var.project_name
    ManagedBy = "terraform"
  }
}

# NOTE: The secret value is intentionally not set in Terraform.
# After applying, populate it yourself via the AWS Console or CLI:
#
#   aws secretsmanager put-secret-value \
#     --secret-id tee-time-finder/teeitup \
#     --secret-string '{"username":"...","password":"..."}'
#
# Terraform state must never contain your credentials.