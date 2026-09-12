# The alias is what clients talk to. It pins a published version and attaches the
# Lambda code hook, so a bad model change never reaches live traffic until the
# alias moves.
resource "aws_lexv2models_bot_alias" "live" {
  bot_alias_name = var.environment
  bot_id         = aws_lexv2models_bot.this.id
  bot_version    = aws_lexv2models_bot_version.this.bot_version
  description    = "Alias used by clients"

  bot_alias_locale_settings {
    locale_id = var.locale_id
    enabled   = true

    code_hook_specification {
      lambda_code_hook {
        lambda_arn                  = aws_lambda_function.fulfillment.arn
        code_hook_interface_version = "1.0"
      }
    }
  }

  conversation_log_settings {
    text_log_settings {
      enabled = true

      destination {
        cloudwatch {
          cloudwatch_log_group_arn = aws_cloudwatch_log_group.conversations.arn
          log_prefix               = "${local.name}/"
        }
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "conversations" {
  name              = "/aws/lex/${local.name}"
  retention_in_days = 30
}
