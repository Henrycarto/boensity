# ECS Fargate services.
#
# Four task definitions from three images: the three FastAPI services plus a
# Celery worker that runs the ingest image with a different command. The worker
# is separate because OCR load and API load have nothing to do with each other.
# A clinic backfilling fifteen years of scans should scale workers without
# touching the web tier.

locals {
  services = {
    timeline = {
      port           = 8002
      path_pattern   = "/trajectory*"
      cpu            = 1024
      memory         = 2048
      desired_count  = var.environment == "production" ? 2 : 1
      health_path    = "/health"
    }
    ingest = {
      port          = 8001
      path_pattern  = "/upload*"
      cpu           = 512
      memory        = 1024
      desired_count = var.environment == "production" ? 2 : 1
      health_path   = "/health"
    }
    sync = {
      port          = 8003
      path_pattern  = "/adherence*"
      cpu           = 512
      memory        = 1024
      desired_count = var.environment == "production" ? 2 : 1
      health_path   = "/health"
    }
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  # The API tier stays on on-demand. Spot reclaiming a task mid-request is
  # acceptable for a queue worker and not for a clinician waiting on a chart.
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

resource "aws_ecr_repository" "service" {
  for_each = toset(["timeline", "ingest", "sync", "web"])

  name                 = "boensity/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  # Scanning is not optional on an image that will process patient documents.
  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 30 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "load_balancer" {
  name        = "${local.name}-alb"
  description = "Public load balancer"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.load_balancer.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS"
}

# Port 80 exists only to redirect. Nothing is served over it.
resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  security_group_id = aws_security_group.load_balancer.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP, redirected to HTTPS"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_services" {
  security_group_id            = aws_security_group.load_balancer.id
  referenced_security_group_id = aws_security_group.service.id
  ip_protocol                  = "-1"
  description                  = "To the service tier"
}

resource "aws_security_group" "service" {
  name        = "${local.name}-service"
  description = "ECS tasks"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-service" }
}

resource "aws_vpc_security_group_ingress_rule" "service_from_alb" {
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.load_balancer.id
  from_port                    = 8001
  to_port                      = 8003
  ip_protocol                  = "tcp"
  description                  = "From the load balancer"
}

# Services call each other: ingest posts confirmed scans to timeline.
resource "aws_vpc_security_group_ingress_rule" "service_from_service" {
  security_group_id            = aws_security_group.service.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 8001
  to_port                      = 8003
  ip_protocol                  = "tcp"
  description                  = "Service to service"
}

resource "aws_vpc_security_group_egress_rule" "service_egress" {
  security_group_id = aws_security_group.service.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Cloud Vision, pharmacy portals, FHIR servers, ECR"
}

# ---------------------------------------------------------------------------
# Load balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = local.name
  load_balancer_type = "application"
  security_groups    = [aws_security_group.load_balancer.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true
  enable_deletion_protection = var.environment == "production"

  access_logs {
    bucket  = aws_s3_bucket.audit_logs.bucket
    prefix  = "alb"
    enabled = true
  }
}

resource "aws_lb_target_group" "service" {
  for_each = local.services

  name        = "${local.name}-${each.key}"
  port        = each.value.port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = each.value.health_path
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # A trajectory request completes in milliseconds, so a long drain only slows
  # deploys down.
  deregistration_delay = 30

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate" "main" {
  count = var.domain_name == "" ? 0 : 1

  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_listener" "https" {
  count = var.domain_name == "" ? 0 : 1

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  # TLS 1.2 minimum. A hospital security review will check this line.
  ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = aws_acm_certificate.main[0].arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      status_code  = "404"
      message_body = jsonencode({ data = null, error = { code = "not_found", message = "No route" }, meta = {} })
    }
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener_rule" "service" {
  for_each = var.domain_name == "" ? {} : local.services

  listener_arn = aws_lb_listener.https[0].arn

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service[each.key].arn
  }

  condition {
    path_pattern {
      values = [each.value.path_pattern]
    }
  }
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role pulls secrets at task start. It is distinct from the task
# role, which is what the running application uses.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.database.arn, aws_secretsmanager_secret.application.arn]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# Scoped to the upload prefix rather than the whole bucket. A compromised
# ingest task should not be able to read the audit log it is written into.
data "aws_iam_policy_document" "task" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/raw/*"]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.uploads.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["raw/*"]
    }
  }

  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "storage"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(concat(keys(local.services), ["ingest-worker"]))

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.environment == "production" ? 90 : 14
  kms_key_id        = aws_kms_key.main.arn
}

# ---------------------------------------------------------------------------
# Task definitions and services
# ---------------------------------------------------------------------------

locals {
  common_secrets = [
    { name = "TIMESCALE_DSN", valueFrom = "${aws_secretsmanager_secret.database.arn}:dsn::" },
  ]

  common_environment = [
    { name = "AWS_REGION", value = var.aws_region },
    { name = "S3_UPLOAD_BUCKET", value = aws_s3_bucket.uploads.bucket },
    { name = "REDIS_URL", value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0" },
    { name = "CELERY_BROKER_URL", value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/1" },
    { name = "CELERY_RESULT_BACKEND", value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/2" },
    { name = "TIMELINE_API_URL", value = "http://timeline.${local.name}.internal:8002" },
    { name = "LOG_LEVEL", value = "INFO" },
  ]
}

resource "aws_ecs_task_definition" "service" {
  for_each = local.services

  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    # Graviton. The workload is numpy and scipy arithmetic plus PDF parsing,
    # all of which are well supported on arm64 and cheaper per vCPU there.
    cpu_architecture = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = each.key
    image     = "${aws_ecr_repository.service[each.key].repository_url}:latest"
    essential = true

    portMappings = [{
      containerPort = each.value.port
      protocol      = "tcp"
    }]

    environment = local.common_environment
    secrets     = local.common_secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.service[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:${each.value.port}${each.value.health_path}', timeout=4).status==200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }

    readonlyRootFilesystem = true

    linuxParameters = {
      initProcessEnabled = true
    }
  }])
}

resource "aws_ecs_service" "service" {
  for_each = local.services

  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.service[each.key].arn
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.service[each.key].arn
    container_name   = each.key
    container_port   = each.value.port
  }

  # Roll forward one task at a time with the old ones still serving, and stop
  # automatically if the new version fails its health check.
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 60
  enable_execute_command            = var.environment != "production"

  service_registries {
    registry_arn = aws_service_discovery_service.service[each.key].arn
  }

  lifecycle {
    # The deploy pipeline updates the image tag. Terraform should not revert it
    # on the next plan.
    ignore_changes = [task_definition, desired_count]
  }
}

# ---- Celery worker ----
# No load balancer and no service registry. It pulls from Redis and is reached
# by nothing.
resource "aws_ecs_task_definition" "ingest_worker" {
  family                   = "${local.name}-ingest-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "ingest-worker"
    image     = "${aws_ecr_repository.service["ingest"].repository_url}:latest"
    essential = true
    command   = ["celery", "-A", "app.tasks.ocr_job.celery_app", "worker", "--loglevel=info", "--concurrency=4"]

    environment = local.common_environment
    secrets     = local.common_secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.service["ingest-worker"].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "ingest_worker" {
  name            = "ingest-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.ingest_worker.arn
  desired_count   = var.environment == "production" ? 2 : 1

  # Workers tolerate interruption: an acks_late task returns to the queue and
  # another worker picks it up, so Spot is the right price point here.
  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

# ---------------------------------------------------------------------------
# Service discovery
# ---------------------------------------------------------------------------

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "${local.name}.internal"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "service" {
  for_each = local.services

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

# ---------------------------------------------------------------------------
# Autoscaling
# ---------------------------------------------------------------------------

resource "aws_appautoscaling_target" "service" {
  for_each = var.environment == "production" ? local.services : {}

  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.service[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 2
  max_capacity       = 10
}

resource "aws_appautoscaling_policy" "service_cpu" {
  for_each = aws_appautoscaling_target.service

  name               = "${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = each.value.service_namespace
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    target_value = 65
    # Slow to scale in. A clinic's load is bursty around clinic hours, and
    # dropping capacity between two bursts just pays the cold start twice.
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

output "ecr_repositories" {
  value = { for key, repo in aws_ecr_repository.service : key => repo.repository_url }
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}
