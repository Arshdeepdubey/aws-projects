output "application_name" {
  value = aws_elastic_beanstalk_application.this.name
}

output "environment_name" {
  value = aws_elastic_beanstalk_environment.this.name
}

output "environment_url" {
  description = "Public URL of the environment."
  value       = "http://${aws_elastic_beanstalk_environment.this.cname}"
}

output "version_label" {
  value = aws_elastic_beanstalk_application_version.this.name
}
