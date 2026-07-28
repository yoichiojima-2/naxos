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

variable "roles" {
  type    = set(string)
  default = ["ops", "analyst"]
}

variable "audit_dataset" {
  type    = string
  default = "audit"
}

provider "google" {
  project = var.project
  region  = var.region
}

resource "google_storage_bucket" "main" {
  name     = var.project
  location = var.region
}

resource "google_bigquery_dataset" "audit" {
  dataset_id = var.audit_dataset
  location   = var.region
}

resource "google_cloud_run_v2_job" "runner" {
  name     = "naxos-runner"
  location = var.region

  template {
    template {
      service_account = google_service_account.role["ops"].email
      max_retries     = 0
      timeout         = "1800s"

      containers {
        image = "asia-northeast1-docker.pkg.dev/naxos-503510/cloud-run-source-deploy/naxos-runner"
        args  = ["Query audit.runs and report how many agent runs happened today and their total cost. One sentence."]

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
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].template[0].containers[0].image]
  }
}

resource "google_service_account" "role" {
  for_each     = var.roles
  account_id   = "sa-role-${each.key}"
  display_name = "naxos agent runner (${each.key})"
}

resource "google_project_iam_member" "aiplatform_user" {
  for_each = var.roles
  project  = var.project
  role     = "roles/aiplatform.user"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  for_each = var.roles
  project  = var.project
  role     = "roles/bigquery.jobUser"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_bigquery_dataset_iam_member" "audit_writer" {
  for_each   = var.roles
  dataset_id = var.audit_dataset
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_storage_bucket_iam_member" "bucket_reader" {
  for_each = var.roles
  bucket   = var.project
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "anthropic-api-key"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "anthropic_api_key_accessor" {
  for_each  = var.roles
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role[each.key].email}"
}
