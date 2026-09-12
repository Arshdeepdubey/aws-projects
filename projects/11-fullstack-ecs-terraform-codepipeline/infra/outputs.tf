output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alb_url" {
  description = "Where the app is reachable."
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.this.dns_name}"
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "web_service_name" {
  value = aws_ecs_service.web.name
}

output "ecr_repositories" {
  value = { for key, repo in aws_ecr_repository.this : key => repo.repository_url }
}

output "pipeline_name" {
  value = var.enable_pipeline ? aws_codepipeline.this[0].name : "(pipeline disabled)"
}

output "todos_table" {
  value = aws_dynamodb_table.todos.name
}

output "vpc_id" {
  value = aws_vpc.this.id
}
