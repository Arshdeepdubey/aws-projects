resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public load balancer"
  vpc_id      = aws_vpc.this.id

  ingress {
    description      = "HTTP"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  dynamic "ingress" {
    for_each = var.domain_name != "" ? [1] : []
    content {
      description      = "HTTPS"
      from_port        = 443
      to_port          = 443
      protocol         = "tcp"
      cidr_blocks      = ["0.0.0.0/0"]
      ipv6_cidr_blocks = ["::/0"]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb" }
}

# Tasks accept traffic only from the ALB's security group — not from a CIDR,
# so a future subnet change cannot silently widen access.
resource "aws_security_group" "service" {
  name        = "${local.name}-service"
  description = "ECS tasks"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-service" }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "API port from the ALB"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.api.port
  to_port                      = var.api.port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.service.id
  description                  = "Web port from the ALB"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.web.port
  to_port                      = var.web.port
  ip_protocol                  = "tcp"
}
