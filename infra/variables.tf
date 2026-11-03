variable "aws_region" {
  description = "AWS region for the application resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used in resource names and tags."
  type        = string
  default     = "tee-time-finder"
}

variable "public_base_url" {
  description = "Public HTTPS URL used by the Cognito managed login callback."
  type        = string
  default     = "https://uod7n5ou71.execute-api.us-east-1.amazonaws.com"
}

