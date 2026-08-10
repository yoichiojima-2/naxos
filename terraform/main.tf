terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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

data "google_project" "this" {
  project_id = var.project_id
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
    "iamcredentials.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"
}

resource "google_project_service" "enabled" {
  for_each                   = toset(local.services)
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# --- images -----------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  repository_id = "naxos"
  format        = "DOCKER"
  location      = var.region

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "drop-old"
    action = "DELETE"
    condition {
      older_than = "2592000s"
    }
  }

  depends_on = [google_project_service.enabled]
}

# --- state ------------------------------------------------------------------
# Public IP + the Cloud Run Cloud SQL connector: no VPC, no NAT, no connector
# fees. Access control is IAM (cloudsql.client) + the db password; there are
# no authorized networks, so nothing can reach the instance directly.

resource "google_sql_database_instance" "state" {
  name                = "naxos-state"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true

  settings {
    tier              = var.db_tier
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_type         = "PD_HDD"
    disk_size         = 10
    disk_autoresize   = false

    ip_configuration {
      ipv4_enabled = true
    }

    backup_configuration {
      enabled = true
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_sql_database" "naxos" {
  name     = "naxos"
  instance = google_sql_database_instance.state.name
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_user" "api" {
  name     = "naxos"
  instance = google_sql_database_instance.state.name
  password = random_password.db.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "database-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = join("", [
    "postgresql://${google_sql_user.api.name}:${random_password.db.result}",
    "@/naxos?host=/cloudsql/${google_sql_database_instance.state.connection_name}",
  ])
}

# --- audit ------------------------------------------------------------------

resource "google_bigquery_dataset" "audit" {
  dataset_id = "naxos_audit"
  location   = var.region
  depends_on = [google_project_service.enabled]
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
  account_id   = "naxos2-api"
  display_name = "naxos control plane"
}

resource "google_service_account" "egress" {
  account_id   = "naxos2-egress"
  display_name = "naxos egress proxy (sole reader of vault secrets)"
}

resource "google_service_account" "scheduler" {
  account_id   = "naxos2-scheduler"
  display_name = "naxos scheduler"
}

resource "google_service_account" "environment" {
  for_each     = local.environments
  account_id   = "naxos2-env-${each.key}"
  display_name = "naxos sandbox: ${each.key}"
}

resource "google_project_iam_member" "api" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/run.developer",
    "roles/cloudscheduler.admin",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.api.email}"
}

# jobs.run on the sandbox jobs requires actAs on their runtime SAs.
resource "google_service_account_iam_member" "api_actas_environment" {
  for_each           = local.environments
  service_account_id = google_service_account.environment[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

resource "google_service_account_iam_member" "api_actas_scheduler" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

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
  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "anthropic_key_env" {
  for_each  = local.environments
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.environment[each.key].email}"
}

resource "google_secret_manager_secret_iam_member" "database_url_api" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# --- control plane services -------------------------------------------------
# Both services authenticate callers with Cloud Run IAM; "internal" is not
# VPC-gated (that would force NAT for sandbox egress). OIDC + invoker bindings
# carry the boundary instead.

resource "google_cloud_run_v2_service" "api" {
  name     = "naxos-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email
    timeout         = "3600s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
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
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "GCLOUD_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "AUDIT_DATASET"
        value = google_bigquery_dataset.audit.dataset_id
      }
      env {
        name  = "SCHEDULER_SA"
        value = google_service_account.scheduler.email
      }
      env {
        name  = "EGRESS_SA"
        value = google_service_account.egress.email
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

  depends_on = [google_secret_manager_secret_version.database_url]
}

resource "google_cloud_run_v2_service" "internal" {
  name     = "naxos-internal"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 3
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
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "GCLOUD_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "AUDIT_DATASET"
        value = google_bigquery_dataset.audit.dataset_id
      }
      env {
        name  = "SCHEDULER_SA"
        value = google_service_account.scheduler.email
      }
      env {
        name  = "EGRESS_SA"
        value = google_service_account.egress.email
      }
      env {
        name  = "ENFORCE_CALLER_AUTH"
        value = "1"
      }
      env {
        name  = "INTERNAL_URL"
        value = "https://naxos-internal-${data.google_project.this.number}.${var.region}.run.app"
      }
      env {
        name  = "EGRESS_URL"
        value = "https://naxos-egress-${data.google_project.this.number}.${var.region}.run.app"
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

  depends_on = [google_secret_manager_secret_version.database_url]
}

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


resource "google_cloud_run_v2_service" "egress" {
  name     = "naxos-egress"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.egress.email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    containers {
      image = local.placeholder_image
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      env {
        name  = "INTERNAL_URL"
        value = google_cloud_run_v2_service.internal.uri
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

resource "google_cloud_run_v2_service_iam_member" "egress_env_invoker" {
  for_each = local.environments
  name     = google_cloud_run_v2_service.egress.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.environment[each.key].email}"
}

resource "google_cloud_run_v2_service_iam_member" "internal_egress_invoker" {
  name     = google_cloud_run_v2_service.internal.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.egress.email}"
}

# The control plane creates one secret per vault credential and grants
# sa-egress accessor on it; that needs secret admin.
resource "google_project_iam_member" "api_secret_admin" {
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.api.email}"
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

  depends_on = [google_project_service.enabled]
}

# --- CI deploy identity (optional) ------------------------------------------

resource "google_service_account" "deployer" {
  count        = var.github_repository != "" ? 1 : 0
  account_id   = "naxos2-deployer"
  display_name = "naxos CI deployer"
}

resource "google_iam_workload_identity_pool" "github" {
  count                     = var.github_repository != "" ? 1 : 0
  workload_identity_pool_id = "naxos-github"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.github_repository != "" ? 1 : 0
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
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
  count              = var.github_repository != "" ? 1 : 0
  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deployer" {
  for_each = var.github_repository != "" ? toset([
    "roles/run.developer",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
  ]) : toset([])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer[0].email}"
}

# --- budget (optional) ------------------------------------------------------

resource "google_billing_budget" "monthly" {
  count           = var.billing_account != "" ? 1 : 0
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

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "internal_url" {
  value = google_cloud_run_v2_service.internal.uri
}

output "egress_url" {
  value = google_cloud_run_v2_service.egress.uri
}

output "artifact_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
