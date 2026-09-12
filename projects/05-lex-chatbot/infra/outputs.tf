output "bot_id" {
  value = aws_lexv2models_bot.this.id
}

output "bot_name" {
  value = aws_lexv2models_bot.this.name
}

output "bot_version" {
  value = aws_lexv2models_bot_version.this.bot_version
}

output "bot_alias_id" {
  value = aws_lexv2models_bot_alias.live.bot_alias_id
}

output "fulfillment_function_name" {
  value = aws_lambda_function.fulfillment.function_name
}

output "bookings_table" {
  value = aws_dynamodb_table.bookings.name
}
