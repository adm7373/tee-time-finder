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

variable "alert_email" {
  description = "Address for budget notifications. Empty creates the budget without emails."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly cost ceiling for this project, in USD."
  type        = number
  default     = 5
}

variable "budget_action_threshold_percent" {
  description = "Percentage of the budget at which the stop-workers action fires."
  type        = number
  default     = 400
}
