# Boensity core infrastructure.
#
# Network, storage, secrets, and the shared pieces the ECS and RDS stacks build
# on. Split across three files by lifecycle rather than by resource type:
# main.tf changes rarely, ecs.tf changes on every deploy shape change, and
# rds.tf is the one a DBA needs to read without wading through the rest.

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Configured per environment through -backend-config. Patient-adjacent
  # infrastructure state does not belong in a local file on someone's laptop.
  backend "s3" {
    key     = "boensity/terraform.tfstate"
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "boensity"
      Environment = var.environment
      ManagedBy   = "terraform"
      # Tagged on everything so a compliance review can enumerate which
      # resources are in scope for a data processing agreement.
      DataClass = "phi"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region" {
  type        = string
  default     = "eu-central-1"
  description = "Region. Keep patient data in the jurisdiction it was collected in."
}

variable "environment" {
  type    = string
  default = "staging"

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "availability_zone_count" {
  type        = number
  default     = 2
  description = "RDS multi-AZ needs at least two."
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "Optional. When set, an ACM certificate and HTTPS listener are created."
}

locals {
  name = "boensity-${var.environment}"

  # Public subnets carry only the load balancer. Every service and the database
  # sit in private subnets with no route to an internet gateway.
  public_subnet_cidrs  = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 8, index)]
  private_subnet_cidrs = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 8, index + 10)]
  data_subnet_cidrs    = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 8, index + 20)]
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = var.availability_zone_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${count.index}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${local.name}-private-${count.index}", Tier = "application" }
}

resource "aws_subnet" "data" {
  count = var.availability_zone_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = local.data_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${local.name}-data-${count.index}", Tier = "data" }
}

resource "aws_eip" "nat" {
  count  = var.environment == "production" ? var.availability_zone_count : 1
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-${count.index}" }
}

# Production runs a NAT gateway per AZ so losing one zone does not take the
# other zone's outbound traffic with it. Staging runs a single gateway, because
# the redundancy is not worth the hourly cost there.
resource "aws_nat_gateway" "main" {
  count = var.environment == "production" ? var.availability_zone_count : 1

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  depends_on    = [aws_internet_gateway.main]

  tags = { Name = "${local.name}-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = var.availability_zone_count
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[var.environment == "production" ? count.index : 0].id
  }

  tags = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_route_table_association" "private" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# The data tier has no default route at all. The database cannot reach the
# internet even if something in it is compromised.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-data" }
}

resource "aws_route_table_association" "data" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# S3 through a gateway endpoint rather than the NAT gateway. Raw PDF uploads
# are the highest volume traffic in the system, and routing them through NAT
# would be both slower and billed per gigabyte.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.data.id])

  tags = { Name = "${local.name}-s3" }
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "uploads" {
  bucket = "${local.name}-raw-uploads"
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.main.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

# Versioning is on because extraction is a model and models get revised. It is
# also the recovery path if a reprocessing bug overwrites a source document.
resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket     = aws_s3_bucket.uploads.id
  depends_on = [aws_s3_bucket_versioning.uploads]

  rule {
    id     = "archive-raw-scans"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    # Source documents are read constantly during the first month while a
    # clinic backfills, then almost never except for audit.
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 365
      storage_class = "GLACIER_IR"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_s3_bucket_logging" "uploads" {
  bucket        = aws_s3_bucket.uploads.id
  target_bucket = aws_s3_bucket.audit_logs.id
  target_prefix = "s3-access/"
}

# Access to patient documents has to be reconstructable after the fact.
resource "aws_s3_bucket" "audit_logs" {
  bucket = "${local.name}-audit-logs"
}

resource "aws_s3_bucket_public_access_block" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_object_lock_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 2555
    }
  }
}

# ---------------------------------------------------------------------------
# Encryption and secrets
# ---------------------------------------------------------------------------

resource "aws_kms_key" "main" {
  description             = "${local.name} data at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}

resource "random_password" "database" {
  length  = 40
  special = true
  # RDS rejects these in a master password.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "database" {
  name       = "${local.name}/database"
  kms_key_id = aws_kms_key.main.arn
}

resource "aws_secretsmanager_secret_version" "database" {
  secret_id = aws_secretsmanager_secret.database.id

  secret_string = jsonencode({
    username = aws_db_instance.timescale.username
    password = random_password.database.result
    host     = aws_db_instance.timescale.address
    port     = aws_db_instance.timescale.port
    dbname   = aws_db_instance.timescale.db_name
    dsn      = "postgresql://${aws_db_instance.timescale.username}:${urlencode(random_password.database.result)}@${aws_db_instance.timescale.endpoint}/${aws_db_instance.timescale.db_name}"
  })
}

# Created empty and populated out of band. Terraform state would otherwise hold
# a Google service account key and a pharmacy network credential in plaintext.
resource "aws_secretsmanager_secret" "application" {
  name        = "${local.name}/application"
  kms_key_id  = aws_kms_key.main.arn
  description = "Vision service account, FHIR client secret, pharmacy API key. Populated outside Terraform."
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "vpc_id" {
  value = aws_vpc.main.id
}

output "uploads_bucket" {
  value = aws_s3_bucket.uploads.bucket
}

output "kms_key_arn" {
  value = aws_kms_key.main.arn
}

output "load_balancer_dns" {
  value = aws_lb.main.dns_name
}

output "database_endpoint" {
  value     = aws_db_instance.timescale.endpoint
  sensitive = true
}
