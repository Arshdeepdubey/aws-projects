variable "project" {
  type        = string
  description = "Project name, used as a resource name prefix."
  default     = "eb-flask"
}

variable "environment" {
  type        = string
  description = "Deployment environment (dev, stage, prod)."
  default     = "dev"
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "solution_stack_name" {
  type        = string
  description = "EB platform. List current ones with: aws elasticbeanstalk list-available-solution-stacks."
  default     = "64bit Amazon Linux 2023 v4.1.2 running Python 3.11"
}

variable "environment_type" {
  type        = string
  description = "LoadBalanced (ALB, multi-AZ) or SingleInstance (cheaper, no ALB)."
  default     = "LoadBalanced"

  validation {
    condition     = contains(["LoadBalanced", "SingleInstance"], var.environment_type)
    error_message = "environment_type must be LoadBalanced or SingleInstance."
  }
}

variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "min_size" {
  type    = number
  default = 1
}

variable "max_size" {
  type    = number
  default = 4
}

variable "app_environment_variables" {
  type        = map(string)
  description = "Extra environment variables passed to the application."
  default     = {}
}
