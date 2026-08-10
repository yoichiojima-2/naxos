terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    bucket = "naxos-503510"
    prefix = "terraform/v2"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  environments = jsondecode(file("${path.module}/environments.json"))
  services = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "bigquery.googleapis.com",
    "cloudscheduler.googleapis.com",
    "aiplatform.googleapis.com",
    "iap.googleapis.com",
    "iamcredentials.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "vpcaccess.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each                   = toset(local.services)
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- network (private IP for Cloud SQL, Direct VPC egress from Cloud Run) ----

resource "google_compute_network" "vpc" {
  name                    = "naxos-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "run" {
  name          = "naxos-run"
  ip_cidr_range = "10.20.0.0/20"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_global_address" "private_ip" {
  name          = "naxos-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

# --- state ------------------------------------------------------------------

resource "google_sql_database_instance" "state" {
  name                = "naxos-state"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true
    }

    backup_configuration {
      enabled = true
    }
  }

  depends_on = [google_service_networking_connection.private_vpc]
}

resource "google_sql_database" "naxos" {
  name     = "naxos"
  instance = google_sql_database_instance.state.name
}

resource "google_sql_user" "api" {
  name     = "naxos-api"
  instance = google_sql_database_instance.state.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

# --- audit ------------------------------------------------------------------

resource "google_bigquery_dataset" "audit" {
  dataset_id = "audit"
  location   = var.region
}

resource "google_bigquery_table" "runs" {
  dataset_id          = google_bigquery_dataset.audit.dataset_id
  table_id            = "runs"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "started_at"
  }
  schema = jsonencode([
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "session_id", type = "STRING", mode = "REQUIRED" },
    { name = "agent_id", type = "STRING" },
    { name = "environment_id", type = "STRING" },
    { name = "deployment_run_id", type = "STRING" },
    { name = "trigger_type", type = "STRING" },
    { name = "principal", type = "STRING" },
    { name = "started_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ended_at", type = "TIMESTAMP" },
    { name = "status", type = "STRING" },
    { name = "stop_reason", type = "STRING" },
    { name = "num_turns", type = "INTEGER" },
    { name = "input_tokens", type = "INTEGER" },
    { name = "output_tokens", type = "INTEGER" },
    { name = "cost_usd", type = "FLOAT" },
    { name = "approx_cost_jpy", type = "FLOAT" },
    { name = "model", type = "STRING" },
  ])
}

resource "google_bigquery_table" "tool_calls" {
  dataset_id          = google_bigquery_dataset.audit.dataset_id
  table_id            = "tool_calls"
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "ts"
  }
  schema = jsonencode([
    { name = "run_id", type = "STRING", mode = "REQUIRED" },
    { name = "session_id", type = "STRING", mode = "REQUIRED" },
    { name = "agent_id", type = "STRING" },
    { name = "principal", type = "STRING" },
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "tool_use_id", type = "STRING" },
    { name = "tool_name", type = "STRING" },
    { name = "args_redacted", type = "STRING" },
    { name = "decision", type = "STRING" },
    { name = "result_status", type = "STRING" },
    { name = "latency_ms", type = "INTEGER" },
    { name = "error", type = "STRING" },
  ])
}

# --- service accounts -------------------------------------------------------

resource "google_service_account" "api" {
  account_id   = "sa-api"
  display_name = "naxos control plane"
}

resource "google_service_account" "egress" {
  account_id   = "sa-egress"
  display_name = "naxos egress proxy (sole reader of vault secrets)"
}

resource "google_service_account" "scheduler" {
  account_id   = "sa-scheduler"
  display_name = "naxos scheduler"
}

resource "google_service_account" "deployer" {
  account_id   = "sa-deployer"
  display_name = "naxos CI deployer"
}

resource "google_service_account" "environment" {
  for_each     = local.environments
  account_id   = "sa-env-${each.key}"
  display_name = "naxos sandbox: ${each.key}"
}

# Control plane: state, audit, and the right to start sandbox jobs.
resource "google_project_iam_member" "api" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/run.invoker",
    "roles/cloudscheduler.admin",
    "roles/iam.serviceAccountUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Sandboxes reach Vertex and nothing else at project level.
resource "google_project_iam_member" "environment_vertex" {
  for_each = local.environments
  project  = var.project_id
  role     = "roles/aiplatform.user"
  member   = "serviceAccount:${google_service_account.environment[each.key].email}"
}

# --- per-environment session buckets ----------------------------------------

resource "google_storage_bucket" "sessions" {
  for_each                    = local.environments
  name                        = "${var.project_id}-sess-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "sessions_env" {
  for_each = local.environments
  bucket   = google_storage_bucket.sessions[each.key].name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.environment[each.key].email}"
}

resource "google_storage_bucket_iam_member" "sessions_api" {
  for_each = local.environments
  bucket   = google_storage_bucket.sessions[each.key].name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.api.email}"
}

# --- secrets ----------------------------------------------------------------

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "anthropic-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "anthropic_key_env" {
  for_each  = local.environments
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.environment[each.key].email}"
}

# --- control plane services -------------------------------------------------

locals {
  db_url            = "postgresql://${google_sql_user.api.name}@/naxos?host=/cloudsql/${google_sql_database_instance.state.connection_name}"
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"
}

resource "google_cloud_run_v2_service" "api" {
  name     = "naxos-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email
    timeout         = "3600s"
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.run.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.state.connection_name]
      }
    }
    containers {
      image = local.placeholder_image
      args  = ["api"]
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "DATABASE_URL"
        value = local.db_url
      }
      env {
        name  = "GCLOUD_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      launch_stage,
    ]
  }
}

resource "google_cloud_run_v2_service" "internal" {
  name     = "naxos-internal"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.api.email
    timeout         = "3600s"
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.run.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.state.connection_name]
      }
    }
    containers {
      image = local.placeholder_image
      args  = ["internal"]
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "DATABASE_URL"
        value = local.db_url
      }
      env {
        name  = "GCLOUD_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      launch_stage,
    ]
  }
}

# Sandboxes and the scheduler are the only callers of the internal surface.
resource "google_cloud_run_v2_service_iam_member" "internal_env_invoker" {
  for_each = local.environments
  name     = google_cloud_run_v2_service.internal.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.environment[each.key].email}"
}

resource "google_cloud_run_v2_service_iam_member" "internal_scheduler_invoker" {
  name     = google_cloud_run_v2_service.internal.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_service_iam_member" "api_iap" {
  count    = length(var.iap_members) > 0 ? 1 : 0
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_iap_web_cloud_run_service_iam_member" "members" {
  for_each               = toset(var.iap_members)
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.api.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

# --- per-environment sandbox job --------------------------------------------

resource "google_cloud_run_v2_job" "sandbox" {
  for_each = local.environments
  name     = "naxos-sbx-${each.key}"
  location = var.region

  template {
    task_count = 1
    template {
      service_account = google_service_account.environment[each.key].email
      max_retries     = 0
      timeout         = "3600s"
      vpc_access {
        network_interfaces {
          network    = google_compute_network.vpc.id
          subnetwork = google_compute_subnetwork.run.id
        }
        egress = "ALL_TRAFFIC"
      }
      containers {
        image = local.placeholder_image
        resources {
          limits = {
            cpu    = each.value.cpu
            memory = each.value.memory
          }
        }
        env {
          name  = "INTERNAL_URL"
          value = google_cloud_run_v2_service.internal.uri
        }
        env {
          name  = "GCLOUD_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "ENVIRONMENT_NAME"
          value = each.key
        }
        env {
          name = "ANTHROPIC_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.anthropic_api_key.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
      launch_stage,
    ]
  }
}

resource "google_cloud_run_v2_job_iam_member" "sandbox_runner" {
  for_each = local.environments
  name     = google_cloud_run_v2_job.sandbox[each.key].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.api.email}"
}

# --- reconciler -------------------------------------------------------------

resource "google_cloud_scheduler_job" "reconcile" {
  name      = "naxos-reconcile"
  schedule  = "* * * * *"
  time_zone = "Asia/Tokyo"
  region    = var.region

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.internal.uri}/internal/reconcile"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.internal.uri
    }
  }
}

# --- CI deploy identity -----------------------------------------------------

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "naxos-github"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_condition                = "assertion.repository == \"${var.github_repository}\""
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/run.developer",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# --- budget -----------------------------------------------------------------

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "naxos monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "JPY"
      units         = tostring(var.budget_jpy)
    }
  }

  threshold_rules {
    threshold_percent = 0.7
  }
  threshold_rules {
    threshold_percent = 1.0
  }
}
