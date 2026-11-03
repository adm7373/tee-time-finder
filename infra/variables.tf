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

