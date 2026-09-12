variable "project" {
  type    = string
  default = "fargate-web"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.30.0.0/16"
}

variable "enable_nat_gateway" {
  type    = bool
  default = true
}

variable "assign_public_ip" {
  type        = bool
  description = "Run tasks in public subnets instead of behind NAT. Dev only."
  default     = false
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "task_cpu" {
  type    = number
  default = 256
}

variable "task_memory" {
  type    = number
  default = 512
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "min_capacity" {
  type    = number
  default = 2
}

variable "max_capacity" {
  type    = number
  default = 10
}

variable "use_fargate_spot" {
  type        = bool
  description = "Run tasks on Fargate Spot (~70% cheaper, two-minute interruption notice)."
  default     = false
}

variable "image_tag" {
  type        = string
  description = "Image tag to run. The deploy script overrides this per release."
  default     = ""
}

variable "deployment_config" {
  type        = string
  description = "CodeDeploy traffic shifting strategy."
  default     = "CodeDeployDefault.ECSCanary10Percent5Minutes"
}

variable "termination_wait_minutes" {
  type        = number
  description = "How long the old (blue) task set stays up after the shift, for instant rollback."
  default     = 5
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_email" {
  type    = string
  default = ""
}
