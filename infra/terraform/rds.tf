# TimescaleDB on RDS PostgreSQL.
#
# RDS carries the timescaledb extension in its supported list, so the
# hypertables, continuous aggregates, and compression policies the Timeline
# service depends on all work on managed Postgres. The alternative, self
# managing Timescale on EC2, would mean owning patching and backups for a
# database holding patient data, which is the wrong thing to own.

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.data[*].id

  tags = { Name = "${local.name}-db" }
}

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "TimescaleDB. Reachable only from the service tier."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-database" }
}

# Referenced by security group rather than by CIDR. A task that is not a
# Boensity service cannot reach the database even from inside the VPC.
resource "aws_vpc_security_group_ingress_rule" "database_from_services" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from ECS services"
}

resource "aws_db_parameter_group" "timescale" {
  name   = "${local.name}-timescale"
  family = "postgres16"

  parameter {
    name         = "shared_preload_libraries"
    value        = "timescaledb,pg_stat_statements"
    apply_method = "pending-reboot"
  }

  # Trajectory queries read a patient's whole series and cohort queries sweep a
  # time window. Both are read heavy against a small working set, so the cache
  # assumption should reflect that rather than the default.
  parameter {
    name  = "effective_cache_size"
    value = "{DBInstanceClassMemory*3/4/8192}"
  }

  parameter {
    name  = "random_page_cost"
    value = "1.1"
  }

  # Anything slower than a second on this workload is a bug worth seeing.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  # Never log statement text. Query parameters here are patient identifiers and
  # clinical values, and logs are the easiest place to leak them.
  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "timescale" {
  identifier     = "${local.name}-timescale"
  engine         = "postgres"
  engine_version = "16.4"

  instance_class = var.environment == "production" ? "db.r6g.xlarge" : "db.t4g.medium"

  # gp3 rather than gp2 so IOPS are provisioned independently of size. The
  # dataset is small and the query rate is not, which is exactly the case gp2
  # handles badly.
  allocated_storage     = var.environment == "production" ? 200 : 50
  max_allocated_storage = var.environment == "production" ? 1000 : 200
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.main.arn

  db_name  = "boensity"
  username = "boensity_app"
  password = random_password.database.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  parameter_group_name   = aws_db_parameter_group.timescale.name
  publicly_accessible    = false

  multi_az = var.environment == "production"

  backup_retention_period = var.environment == "production" ? 30 : 7
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot   = true

  # Point in time recovery matters more here than elsewhere: a bad ingest run
  # can write wrong T-scores across many patients, and the fix is rewinding to
  # just before it rather than reconstructing by hand.
  delete_automated_backups  = false
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${local.name}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}" : null

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.main.arn
  performance_insights_retention_period = var.environment == "production" ? 731 : 7

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  auto_minor_version_upgrade = true
  apply_immediately          = var.environment != "production"

  lifecycle {
    ignore_changes = [final_snapshot_identifier]
  }
}

# Read replica for the cohort screen. The department view scans every patient
# in a time window, and that query should not compete with the writes coming
# off an ingest backfill.
resource "aws_db_instance" "timescale_replica" {
  count = var.environment == "production" ? 1 : 0

  identifier          = "${local.name}-timescale-replica"
  replicate_source_db = aws_db_instance.timescale.identifier
  instance_class      = "db.r6g.large"

  publicly_accessible = false
  storage_encrypted   = true
  kms_key_id          = aws_kms_key.main.arn

  performance_insights_enabled = true
  skip_final_snapshot          = true

  auto_minor_version_upgrade = true
}

# ---------------------------------------------------------------------------
# ElastiCache
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name}-cache"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_security_group" "cache" {
  name        = "${local.name}-cache"
  description = "Redis for the Celery broker and result backend."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-cache" }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_services" {
  security_group_id            = aws_security_group.cache.id
  referenced_security_group_id = aws_security_group.service.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  description                  = "Redis from ECS services"
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-redis"
  description          = "Celery broker and result backend"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.environment == "production" ? "cache.r7g.large" : "cache.t4g.micro"
  port           = 6379

  num_cache_clusters         = var.environment == "production" ? 2 : 1
  automatic_failover_enabled = var.environment == "production"
  multi_az_enabled           = var.environment == "production"

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.cache.id]

  # A queued OCR job references an S3 key for a patient document, so the queue
  # is in scope for encryption even though it holds no clinical values.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.main.arn

  # Celery results are recomputable from the source PDF, so a snapshot buys
  # nothing worth the write amplification.
  snapshot_retention_limit = 0

  maintenance_window = "sun:05:00-sun:06:00"
  apply_immediately  = var.environment != "production"
}

output "redis_endpoint" {
  value     = aws_elasticache_replication_group.main.primary_endpoint_address
  sensitive = true
}
