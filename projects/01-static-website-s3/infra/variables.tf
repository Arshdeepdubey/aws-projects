variable "project" {
  description = "Project name, used as a resource name prefix."
  type        = string
  default     = "static-site"
}

variable "environment" {
  description = "Deployment environment (dev, stage, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "Region for the S3 buckets."
  type        = string
  default     = "ap-south-1"
}

variable "domain_name" {
  description = "Custom domain for the site, e.g. www.example.com. Empty uses the CloudFront domain."
  type        = string
  default     = ""
}

variable "hosted_zone_id" {
  description = "Route 53 public hosted zone ID for domain_name. Required when domain_name is set."
  type        = string
  default     = ""
}

variable "price_class" {
  description = "CloudFront price class: PriceClass_100 | PriceClass_200 | PriceClass_All."
  type        = string
  default     = "PriceClass_100"
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty buckets. Keep false in production."
  type        = bool
  default     = true
}
