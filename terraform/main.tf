# Atlas Flex cluster for InstructaMate PoC ingest (#34).
# Assumes an existing Atlas project. Does NOT manage the Vector Search index
# (code-ensured at runtime from src/instructamate/data/chunks_vector.json).

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.29"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "mongodbatlas" {
  # Uses MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY env vars
  # (or ATLAS_PUBLIC_KEY / ATLAS_PRIVATE_KEY — see provider docs).
}

variable "project_id" {
  type        = string
  description = "Existing Atlas project ID. Set via TF_VAR_project_id (./tf.sh maps MONGODB_PROJECT_ID from repo .env)."
}

variable "cluster_name" {
  type        = string
  description = "Flex cluster name."
  default     = "instructamate-flex"
}

variable "db_username" {
  type        = string
  description = "Database user with readWrite on instructamate."
  default     = "instructamate"
}

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "mongodbatlas_flex_cluster" "main" {
  project_id = var.project_id
  name       = var.cluster_name
  provider_settings = {
    backing_provider_name = "AWS"
    region_name           = "AP_SOUTHEAST_2"
  }
  termination_protection_enabled = false
}

resource "mongodbatlas_database_user" "ingest" {
  project_id         = var.project_id
  username           = var.db_username
  password           = random_password.db.result
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = "instructamate"
  }

  scopes {
    name = mongodbatlas_flex_cluster.main.name
    type = "CLUSTER"
  }
}

# PoC-open network access — tighten before any shared/prod use.
resource "mongodbatlas_project_ip_access_list" "open" {
  project_id = var.project_id
  cidr_block = "0.0.0.0/0"
  comment    = "PoC open access for InstructaMate ingest (#34)"
}

locals {
  srv_host = replace(
    mongodbatlas_flex_cluster.main.connection_strings.standard_srv,
    "mongodb+srv://",
    ""
  )
}

output "mongodb_uri" {
  description = "Connection string for MONGODB_URI (includes DB user credentials)."
  sensitive   = true
  value = format(
    "mongodb+srv://%s:%s@%s/?retryWrites=true&w=majority",
    var.db_username,
    random_password.db.result,
    local.srv_host
  )
}

output "cluster_name" {
  value = mongodbatlas_flex_cluster.main.name
}

output "mongo_db_version" {
  value = mongodbatlas_flex_cluster.main.mongo_db_version
}
