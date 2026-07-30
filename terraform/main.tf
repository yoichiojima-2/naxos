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
  # deterministic form of the service URL: computable before the service
  # exists, and usable inside the UI service's own env without a self-cycle
  ui_url = "https://naxos-ui-${data.google_project.main.number}.${var.region}.run.app"
}

variable "audit_dataset" {
  type    = string
  default = "audit"
}

variable "admin" {
  type = string
}

variable "billing_account" {
  type = string
}

variable "budget_jpy" {
  type    = number
  default = 2000
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

# fictional-company sample data for agents to analyze; tables are loaded
# out of band by scripts/seed.py, same split as audit.runs
resource "google_bigquery_dataset" "lumen" {
  dataset_id                 = "lumen"
  location                   = var.region
  delete_contents_on_destroy = true
}

resource "google_bigquery_dataset_iam_member" "lumen_reader" {
  for_each   = local.roles
  dataset_id = google_bigquery_dataset.lumen.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.role[each.key].email}"
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
          name  = "UI_URL"
          value = local.ui_url
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
  for_each = local.roles
  name     = google_cloud_run_v2_job.runner[each.key].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
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

data "google_project" "main" {
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "naxos monthly"

  budget_filter {
    projects = ["projects/${data.google_project.main.number}"]
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

resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project}-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
}

# objectCreator only: published artifacts cannot be overwritten or deleted
resource "google_storage_bucket_iam_member" "artifact_creator" {
  for_each = local.roles
  bucket   = google_storage_bucket.artifacts.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_storage_bucket_iam_member" "artifacts_admin" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = var.admin
}

resource "google_service_account" "ui" {
  account_id   = "sa-naxos-ui"
  display_name = "naxos UI (interactive runs; union of role grants until the runner service split)"
}

resource "google_project_iam_member" "ui_aiplatform_user" {
  project = var.project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_project_iam_member" "ui_bigquery_job_user" {
  project = var.project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ui.email}"
}

# scheduled tasks (naxos-schedule-*) are user data owned by the UI/gcloud,
# not Terraform; jobs.run backs the UI's run-now button — the run still goes
# through the same runner path (kill switch, audit) as a cron firing
resource "google_project_iam_custom_role" "scheduler_editor" {
  role_id     = "naxosSchedulerEditor"
  title       = "naxos scheduler task editor"
  permissions = [
    "cloudscheduler.jobs.get",
    "cloudscheduler.jobs.list",
    "cloudscheduler.jobs.create",
    "cloudscheduler.jobs.update",
    "cloudscheduler.jobs.delete",
    "cloudscheduler.jobs.pause",
    "cloudscheduler.jobs.enable",
    "cloudscheduler.jobs.run",
    "cloudscheduler.locations.get",
    "cloudscheduler.locations.list",
  ]
}

resource "google_project_iam_member" "ui_scheduler_editor" {
  project = var.project
  role    = google_project_iam_custom_role.scheduler_editor.id
  member  = "serviceAccount:${google_service_account.ui.email}"
}

# updating a job that authenticates as sa-scheduler requires actAs on it
resource "google_service_account_iam_member" "ui_act_as_scheduler" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_bigquery_dataset_iam_member" "ui_audit_writer" {
  dataset_id = var.audit_dataset
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_storage_bucket_iam_member" "ui_bucket_reader" {
  bucket = var.project
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ui.email}"

  condition {
    title      = "everything-but-state"
    expression = "!resource.name.startsWith(\"projects/_/buckets/${var.project}/objects/terraform/\")"
  }
}

# backs the UI's skills editor tab; scoped so the UI can never touch
# state, kill-switch markers, or anything else in the main bucket
resource "google_storage_bucket_iam_member" "ui_skills_editor" {
  bucket = var.project
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.ui.email}"

  condition {
    title      = "skills-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${var.project}/objects/skills/\")"
  }
}

resource "google_storage_bucket_iam_member" "ui_session_user" {
  for_each = local.roles
  bucket   = google_storage_bucket.sessions[each.key].name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_storage_bucket_iam_member" "ui_artifact_creator" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.ui.email}"
}

# viewer for the UI's artifacts browser tab; creator+viewer keeps immutability
resource "google_storage_bucket_iam_member" "ui_artifact_viewer" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_secret_manager_secret_iam_member" "ui_anthropic_api_key_accessor" {
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ui.email}"
}

resource "google_service_account_iam_member" "deployer_act_as_ui" {
  service_account_id = google_service_account.ui.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_cloud_run_v2_service" "ui" {
  name                = "naxos-ui"
  location            = var.region
  deletion_protection = false

  template {
    service_account                  = google_service_account.ui.email
    timeout                          = "1800s"
    max_instance_request_concurrency = 1
    # keep a browser session on the instance holding its live SDK client
    session_affinity = true

    scaling {
      max_instance_count = 5
    }

    containers {
      image   = "${var.region}-docker.pkg.dev/${var.project}/cloud-run-source-deploy/naxos-runner:latest"
      command = ["/app/.venv/bin/uvicorn"]
      args    = ["naxos.api:app", "--host", "0.0.0.0", "--port", "8080"]

      # the live-client pool keeps up to MAX_CLIENTS SDK subprocesses resident
      resources {
        limits = { memory = "2Gi", cpu = "1" }
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
        name  = "IAP_AUDIENCE"
        value = "/projects/${data.google_project.main.number}/locations/${var.region}/services/naxos-ui"
      }
      env {
        name  = "UI_URL"
        value = local.ui_url
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

  lifecycle {
    # any update this resource sends drops the gcloud-set IAP flag (provider
    # 6.x cannot carry the system annotation), so everything that drifts on
    # its own — including the API-materialized empty scaling block — must be
    # ignored to keep applies from touching the service; after a real config
    # change here, re-run: gcloud beta run services update naxos-ui --iap
    ignore_changes = [client, client_version, annotations, launch_stage, scaling, template[0].containers[0].image]
  }
}

resource "google_cloud_run_v2_service_iam_member" "ui_iap_invoker" {
  name     = google_cloud_run_v2_service.ui.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.main.number}@gcp-sa-iap.iam.gserviceaccount.com"
}
