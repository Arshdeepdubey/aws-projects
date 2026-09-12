variable "project" {
  type    = string
  default = "fullstack"
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
  default = "10.20.0.0/16"
}

variable "enable_nat_gateway" {
  type        = bool
  description = "Tasks in private subnets need NAT (or VPC endpoints) to pull images."
  default     = true
}

variable "single_nat_gateway" {
  type        = bool
  description = "One NAT for all AZs. Cheaper, but a single AZ failure takes out egress."
  default     = true
}

variable "assign_public_ip" {
  type        = bool
  description = "Run tasks in public subnets with public IPs instead of using NAT. Dev only."
  default     = false
}

# ------------------------------------------------------------------ services
variable "api" {
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
    min_count     = number
    max_count     = number
    port          = number
    health_path   = string
  })
  default = {
    cpu           = 256
    memory        = 512
    desired_count = 2
    min_count     = 2
    max_count     = 10
    port          = 3000
    health_path   = "/api/health"
  }
}

variable "web" {
  type = object({
    cpu           = number
    memory        = number
    desired_count = number
    min_count     = number
    max_count     = number
    port          = number
    health_path   = string
  })
  default = {
    cpu           = 256
    memory        = 512
    desired_count = 2
    min_count     = 1
    max_count     = 6
    port          = 80
    health_path   = "/health"
  }
}

variable "placeholder_image" {
  type        = string
  description = "Used until the pipeline pushes a real image, so targets are healthy from the start."
  default     = "public.ecr.aws/nginx/nginx:stable"
}

# ------------------------------------------------------------------ pipeline
variable "github_repository" {
  type        = string
  description = "owner/repo that CodePipeline watches."
  default     = ""
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "codestar_connection_arn" {
  type        = string
  description = "CodeStar connection ARN. Create it in the console and complete the handshake."
  default     = ""
}

variable "enable_pipeline" {
  type        = bool
  description = "Set false to deploy only the runtime infrastructure."
  default     = true
}

# --------------------------------------------------------------------- https
variable "domain_name" {
  type        = string
  description = "Custom domain for HTTPS. Empty means HTTP only on the ALB."
  default     = ""
}

variable "hosted_zone_id" {
  type    = string
  default = ""
}

variable "alarm_email" {
  type    = string
  default = ""
}
