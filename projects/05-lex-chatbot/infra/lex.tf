# ---------------------------------------------------------------- bot + role
data "aws_iam_policy_document" "lex_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lexv2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lex" {
  name               = "${local.name}-lex"
  assume_role_policy = data.aws_iam_policy_document.lex_assume.json
}

resource "aws_iam_role_policy_attachment" "lex_polly" {
  role       = aws_iam_role.lex.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonPollyReadOnlyAccess"
}

resource "aws_lexv2models_bot" "this" {
  name                        = local.name
  description                 = "Appointment booking assistant"
  role_arn                    = aws_iam_role.lex.arn
  idle_session_ttl_in_seconds = var.idle_session_ttl_seconds
  type                        = "Bot"

  data_privacy {
    child_directed = false
  }

  tags = local.tags
}

resource "aws_lexv2models_bot_locale" "this" {
  bot_id                           = aws_lexv2models_bot.this.id
  bot_version                      = "DRAFT"
  locale_id                        = var.locale_id
  n_lu_intent_confidence_threshold = var.nlu_confidence_threshold
  description                      = "Primary locale"

  voice_settings {
    voice_id = var.voice_id
    engine   = "neural"
  }
}

# ---------------------------------------------------------------- slot types
resource "aws_lexv2models_slot_type" "appointment_type" {
  bot_id      = aws_lexv2models_bot.this.id
  bot_version = aws_lexv2models_bot_locale.this.bot_version
  locale_id   = aws_lexv2models_bot_locale.this.locale_id
  name        = "AppointmentType"
  description = "Kind of appointment being booked"

  value_selection_setting {
    resolution_strategy = "TopResolution"
  }

  dynamic "slot_type_values" {
    for_each = {
      dental    = ["dentist", "teeth", "dental checkup"]
      physician = ["doctor", "gp", "general physician"]
      eye       = ["optometrist", "eye test", "vision"]
    }

    content {
      sample_value {
        value = slot_type_values.key
      }

      dynamic "synonyms" {
        for_each = slot_type_values.value
        content {
          value = synonyms.value
        }
      }
    }
  }
}

# ---------------------------------------------------------------- intents
resource "aws_lexv2models_intent" "book_appointment" {
  bot_id      = aws_lexv2models_bot.this.id
  bot_version = aws_lexv2models_bot_locale.this.bot_version
  locale_id   = aws_lexv2models_bot_locale.this.locale_id
  name        = "BookAppointment"
  description = "Book a new appointment"

  sample_utterance { utterance = "I want to book an appointment" }
  sample_utterance { utterance = "book an appointment" }
  sample_utterance { utterance = "make a booking" }
  sample_utterance { utterance = "I need to see a {AppointmentType}" }
  sample_utterance { utterance = "book a {AppointmentType} appointment" }
  sample_utterance { utterance = "book a {AppointmentType} appointment on {Date}" }
  sample_utterance { utterance = "schedule {AppointmentType} for {Date} at {Time}" }

  dialog_code_hook {
    enabled = true
  }

  fulfillment_code_hook {
    enabled = true
  }
}

resource "aws_lexv2models_intent" "check_booking" {
  bot_id      = aws_lexv2models_bot.this.id
  bot_version = aws_lexv2models_bot_locale.this.bot_version
  locale_id   = aws_lexv2models_bot_locale.this.locale_id
  name        = "CheckBooking"
  description = "Look up an existing booking"

  sample_utterance { utterance = "check my booking" }
  sample_utterance { utterance = "what is the status of my appointment" }
  sample_utterance { utterance = "look up booking {BookingRef}" }

  fulfillment_code_hook {
    enabled = true
  }
}

resource "aws_lexv2models_intent" "cancel_booking" {
  bot_id      = aws_lexv2models_bot.this.id
  bot_version = aws_lexv2models_bot_locale.this.bot_version
  locale_id   = aws_lexv2models_bot_locale.this.locale_id
  name        = "CancelBooking"
  description = "Cancel an existing booking"

  sample_utterance { utterance = "cancel my appointment" }
  sample_utterance { utterance = "cancel booking {BookingRef}" }
  sample_utterance { utterance = "I can't make my appointment" }

  fulfillment_code_hook {
    enabled = true
  }
}

# ---------------------------------------------------------------- slots
resource "aws_lexv2models_slot" "appointment_type" {
  bot_id       = aws_lexv2models_bot.this.id
  bot_version  = aws_lexv2models_bot_locale.this.bot_version
  locale_id    = aws_lexv2models_bot_locale.this.locale_id
  intent_id    = aws_lexv2models_intent.book_appointment.intent_id
  name         = "AppointmentType"
  description  = "Kind of appointment"
  slot_type_id = aws_lexv2models_slot_type.appointment_type.slot_type_id

  value_elicitation_setting {
    slot_constraint = "Required"

    prompt_specification {
      max_retries                = 2
      allow_interrupt            = true
      message_selection_strategy = "Random"

      message_group {
        message {
          plain_text_message {
            value = "What kind of appointment do you need — dental, physician or eye?"
          }
        }
      }
    }
  }
}

resource "aws_lexv2models_slot" "date" {
  bot_id       = aws_lexv2models_bot.this.id
  bot_version  = aws_lexv2models_bot_locale.this.bot_version
  locale_id    = aws_lexv2models_bot_locale.this.locale_id
  intent_id    = aws_lexv2models_intent.book_appointment.intent_id
  name         = "Date"
  description  = "Date of the appointment"
  slot_type_id = "AMAZON.Date"

  value_elicitation_setting {
    slot_constraint = "Required"

    prompt_specification {
      max_retries     = 2
      allow_interrupt = true

      message_group {
        message {
          plain_text_message {
            value = "What day works for you?"
          }
        }
      }
    }
  }
}

resource "aws_lexv2models_slot" "time" {
  bot_id       = aws_lexv2models_bot.this.id
  bot_version  = aws_lexv2models_bot_locale.this.bot_version
  locale_id    = aws_lexv2models_bot_locale.this.locale_id
  intent_id    = aws_lexv2models_intent.book_appointment.intent_id
  name         = "Time"
  description  = "Time of the appointment"
  slot_type_id = "AMAZON.Time"

  value_elicitation_setting {
    slot_constraint = "Required"

    prompt_specification {
      max_retries     = 2
      allow_interrupt = true

      message_group {
        message {
          plain_text_message {
            value = "What time suits you? We are open ${var.opening_hours.start} to ${var.opening_hours.end}."
          }
        }
      }
    }
  }
}

resource "aws_lexv2models_slot" "name" {
  bot_id       = aws_lexv2models_bot.this.id
  bot_version  = aws_lexv2models_bot_locale.this.bot_version
  locale_id    = aws_lexv2models_bot_locale.this.locale_id
  intent_id    = aws_lexv2models_intent.book_appointment.intent_id
  name         = "Name"
  description  = "Name the booking is under"
  slot_type_id = "AMAZON.FirstName"

  value_elicitation_setting {
    slot_constraint = "Required"

    prompt_specification {
      max_retries     = 2
      allow_interrupt = true

      message_group {
        message {
          plain_text_message {
            value = "And what name should I put the booking under?"
          }
        }
      }
    }
  }
}

resource "aws_lexv2models_slot" "check_booking_ref" {
  bot_id       = aws_lexv2models_bot.this.id
  bot_version  = aws_lexv2models_bot_locale.this.bot_version
  locale_id    = aws_lexv2models_bot_locale.this.locale_id
  intent_id    = aws_lexv2models_intent.check_booking.intent_id
  name         = "BookingRef"
  description  = "Booking reference"
  slot_type_id = "AMAZON.AlphaNumeric"

  value_elicitation_setting {
    slot_constraint = "Required"

    prompt_specification {
      max_retries     = 2
      allow_interrupt = true

      message_group {
        message {
          plain_text_message {
            value = "What is your booking reference? It looks like BK1234."
          }
        }
      }
    }
  }
}

resource "aws_lexv2models_slot" "cancel_booking_ref" {
  bot_id       = aws_lexv2models_bot.this.id
  bot_version  = aws_lexv2models_bot_locale.this.bot_version
  locale_id    = aws_lexv2models_bot_locale.this.locale_id
  intent_id    = aws_lexv2models_intent.cancel_booking.intent_id
  name         = "BookingRef"
  description  = "Booking reference to cancel"
  slot_type_id = "AMAZON.AlphaNumeric"

  value_elicitation_setting {
    slot_constraint = "Required"

    prompt_specification {
      max_retries     = 2
      allow_interrupt = true

      message_group {
        message {
          plain_text_message {
            value = "Which booking reference should I cancel?"
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------- build + alias
# Lex requires an explicit locale build after model changes. Terraform has no
# resource for it, so this shells out and waits for the build to finish.
resource "null_resource" "build_locale" {
  count = var.auto_build ? 1 : 0

  triggers = {
    intents = sha1(join(",", [
      aws_lexv2models_intent.book_appointment.intent_id,
      aws_lexv2models_intent.check_booking.intent_id,
      aws_lexv2models_intent.cancel_booking.intent_id,
      aws_lexv2models_slot.appointment_type.slot_id,
      aws_lexv2models_slot.date.slot_id,
      aws_lexv2models_slot.time.slot_id,
      aws_lexv2models_slot.name.slot_id,
      aws_lexv2models_slot.check_booking_ref.slot_id,
      aws_lexv2models_slot.cancel_booking_ref.slot_id,
    ]))
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      aws lexv2-models build-bot-locale \
        --bot-id ${aws_lexv2models_bot.this.id} \
        --bot-version DRAFT \
        --locale-id ${var.locale_id} \
        --region ${data.aws_region.current.name} >/dev/null

      for _ in $(seq 1 60); do
        status = $(aws lexv2-models describe-bot-locale \
          --bot-id ${aws_lexv2models_bot.this.id} \
          --bot-version DRAFT \
          --locale-id ${var.locale_id} \
          --region ${data.aws_region.current.name} \
          --query botLocaleStatus --output text)
        echo "locale status: $status"
        case "$status" in
          Built) exit 0 ;;
          Failed|NotBuilt) exit 1 ;;
        esac
        sleep 10
      done
      echo "timed out waiting for the locale build" >&2
      exit 1
    EOT
  }
}

resource "aws_lexv2models_bot_version" "this" {
  bot_id = aws_lexv2models_bot.this.id

  locale_specification = {
    (var.locale_id) = {
      source_bot_version = "DRAFT"
    }
  }

  depends_on = [null_resource.build_locale]
}
