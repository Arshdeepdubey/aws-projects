resource "aws_lb" "this" {
  name                       = substr("${local.name}-alb", 0, 32)
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "prod"

  tags = { Name = "${local.name}-alb" }
}

# Blue/green needs two target groups. CodeDeploy swaps which one the production
# listener forwards to; the names must differ but the settings must match.
resource "aws_lb_target_group" "blue" {
  name        = substr("${local.name}-blue", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  # Must be <= the app's shutdown drain timeout, or the ALB cuts connections
  # the app is still finishing.
  deregistration_delay = 30

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    matcher             = "200"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group" "green" {
  name        = substr("${local.name}-green", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  deregistration_delay = 30

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    matcher             = "200"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_listener" "production" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.blue.arn
  }

  # CodeDeploy rewrites the target group on every deployment.
  lifecycle {
    ignore_changes = [default_action]
  }
}

# Lets you exercise the new revision before it takes production traffic.
resource "aws_lb_listener" "test" {
  load_balancer_arn = aws_lb.this.arn
  port              = 8080
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.green.arn
  }

  lifecycle {
    ignore_changes = [default_action]
  }
}
