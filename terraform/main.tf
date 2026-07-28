terraform {
  required_version = ">= 1.5"

  backend "gcs" {
    bucket = "naxos-503510"
    prefix = "terraform/state"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project" {
  type    = string
  default = "naxos-503510"
}

variable "region" {
  type    = string
  default = "asia-northeast1"
}

variable "github_repo" {
  type    = string
  default = "yoichiojima-2/naxos"
}

locals {
  role_config = jsondecode(file("${path.module}/../roles.json"))
  roles       = toset(keys(local.role_config))

  # creation-time seeds only: cron and prompt are day-to-day values, edited
  # via `gcloud scheduler jobs update` and ignored by Terraform after creation
  schedules = {
    ops = {
      schedule = "0 9 * * *"
      prompt   = "Check audit.runs for the past 24 hours and report briefly: run count, total cost, and whether any runs errored. If there are errors or unusually expensive runs, investigate and point out the cause."
    }
  }
}

variable "audit_dataset" {
  type    = string
  default = "audit"
}

variable "admin" {
  type    = string
  default = "user:yoichiojima@gmail.com"
}

variable "billing_account" {
  type    = string
  default = "0131FB-CC98F2-DD8246"
}

variable "budget_jpy" {
  type    = number
  default = 100000
}

provider "google" {
  project = var.project
  region  = var.region

  # billingbudgets has no default quota project under user ADC
  user_project_override = true
  billing_project       = var.project
}

resource "google_storage_bucket" "main" {
  name                        = var.project
  location                    = var.region
  uniform_bucket_level_access = true
}

resource "google_bigquery_dataset" "audit" {
  dataset_id = var.audit_dataset
  location   = var.region
}

resource "google_cloud_run_v2_job" "runner" {
  for_each = local.roles
  name     = "naxos-runner-${each.key}"
  location = var.region

  template {
    template {
      service_account = google_service_account.role[each.key].email
      max_retries     = 0
      timeout         = "1800s"

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project}/cloud-run-source-deploy/naxos-runner:latest"

        env {
          name  = "ROLE"
          value = each.key
        }
        env {
          name  = "BUCKET"
          value = var.project
        }
        env {
          name  = "LOG_LEVEL"
          value = "INFO"
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
        env {
          name = "SLACK_WEBHOOK_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.slack_webhook_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].template[0].containers[0].image]
  }
}

resource "google_service_account" "role" {
  for_each     = local.roles
  account_id   = "sa-role-${each.key}"
  display_name = "naxos agent runner (${each.key})"
}

resource "google_project_iam_member" "aiplatform_user" {
  for_each = local.roles
  project  = var.project
  role     = "roles/aiplatform.user"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  for_each = local.roles
  project  = var.project
  role     = "roles/bigquery.jobUser"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_bigquery_dataset_iam_member" "audit_writer" {
  for_each   = local.roles
  dataset_id = var.audit_dataset
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_storage_bucket_iam_member" "bucket_reader" {
  for_each = local.roles
  bucket   = var.project
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"

  condition {
    title      = "everything-but-state"
    expression = "!resource.name.startsWith(\"projects/_/buckets/${var.project}/objects/terraform/\")"
  }
}

resource "google_storage_bucket_iam_member" "admin" {
  bucket = var.project
  role   = "roles/storage.objectAdmin"
  member = var.admin
}

resource "google_storage_bucket" "sessions" {
  for_each                    = local.roles
  name                        = "${var.project}-sessions-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
}

resource "google_storage_bucket_iam_member" "session_user" {
  for_each = local.roles
  bucket   = google_storage_bucket.sessions[each.key].name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_storage_bucket_iam_member" "sessions_admin" {
  for_each = local.roles
  bucket   = google_storage_bucket.sessions[each.key].name
  role     = "roles/storage.objectAdmin"
  member   = var.admin
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == \"${var.github_repo}\""
}

resource "google_service_account" "deployer" {
  account_id   = "sa-deployer"
  display_name = "GitHub Actions deployer"
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

resource "google_project_iam_member" "deployer_artifact_writer" {
  project = var.project
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_run_developer" {
  project = var.project
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_act_as_role" {
  for_each           = local.roles
  service_account_id = google_service_account.role[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account" "scheduler" {
  account_id   = "sa-scheduler"
  display_name = "Cloud Scheduler trigger"
}

# run.developer, not run.invoker: triggering with containerOverrides (the
# prompt in the request body) requires run.jobs.runWithOverrides
resource "google_cloud_run_v2_job_iam_member" "scheduler_runner" {
  for_each = local.schedules
  name     = google_cloud_run_v2_job.runner[each.key].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "scheduled" {
  for_each  = local.schedules
  name      = "naxos-schedule-${each.key}"
  schedule  = each.value.schedule
  time_zone = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project}/locations/${var.region}/jobs/${google_cloud_run_v2_job.runner[each.key].name}:run"
    headers     = { "Content-Type" = "application/json" }
    body = base64encode(jsonencode({
      overrides = { containerOverrides = [{ args = [each.value.prompt] }] }
    }))

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  lifecycle {
    ignore_changes = [schedule, http_target[0].body]
  }
}

resource "google_secret_manager_secret" "slack_webhook_url" {
  secret_id = "slack-webhook-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "slack_webhook_url_accessor" {
  for_each  = local.roles
  secret_id = google_secret_manager_secret.slack_webhook_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "anthropic-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "anthropic_api_key_accessor" {
  for_each  = local.roles
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "naxos monthly"

  budget_filter {
    projects = ["projects/${var.project}"]
  }

  amount {
    specified_amount {
      units = var.budget_jpy
    }
  }

  # 0.7 = the ¥70k operating target, 1.0 = the ¥100k hard cap
  threshold_rules {
    threshold_percent = 0.7
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }
}
