output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alb_url" {
  value = "http://${aws_lb.this.dns_name}"
}

output "test_listener_url" {
  description = "Exercise the green fleet here before it takes production traffic."
  value       = "http://${aws_lb.this.dns_name}:8080"
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.app.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.app.family
}

output "ecr_repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "codedeploy_app_name" {
  value = aws_codedeploy_app.this.name
}

output "codedeploy_deployment_group" {
  value = aws_codedeploy_deployment_group.this.deployment_group_name
}

output "container_name" {
  value = "app"
}

output "container_port" {
  value = var.container_port
}

output "aws_region" {
  description = "Region the stack is deployed in; used by the helper scripts."
  value       = var.aws_region
}
