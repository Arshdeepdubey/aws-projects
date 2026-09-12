variable "project" {
  type    = string
  default = "booking-bot"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type        = string
  description = "Must be a region where Lex V2 is available."
  default     = "ap-south-1"
}

variable "locale_id" {
  type    = string
  default = "en_US"
}

variable "voice_id" {
  type        = string
  description = "Polly voice used for speech interactions."
  default     = "Joanna"
}

variable "nlu_confidence_threshold" {
  type        = number
  description = "Below this score Lex falls back to FallbackIntent."
  default     = 0.40
}

variable "idle_session_ttl_seconds" {
  type    = number
  default = 600
}

variable "auto_build" {
  type        = bool
  description = "Run build-bot-locale via the AWS CLI after intents change."
  default     = true
}

variable "opening_hours" {
  type = object({
    start = string
    end   = string
  })
  description = "Bookable window, 24h local time, used by the Lambda validator."
  default = {
    start = "09:00"
    end   = "18:00"
  }
}
