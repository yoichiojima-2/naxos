# Self-hosted MCP connectors: upstream OSS servers, one scale-to-zero Cloud Run
# service each. naxos writes no connector logic — these are unmodified upstream
# images mirrored into Artifact Registry by scripts/mirror_connectors.sh.
#
# Credentials are Secret Manager env refs on the connector service, readable
# only by that connector's own service account: the sandbox never holds them,
# and no vault or egress route is involved. The sandbox reaches a connector by
# minting an OIDC token for it (naxos_sbx.mcp_gateway), so the run.invoker
# binding below is the per-environment access knob.
#
# Ingress is ALL for the same reason as naxos-internal: the sandbox job has no
# VPC connector, so "internal" would force NAT. IAM carries the boundary.
#
# Cloud Run forwards the caller's Authorization header to the container, so a
# connector that also wants its own bearer token would see the sandbox's ID
# token and reject it. Cloud Run IAM is the gate instead — hence
# `--unsafe-disable-auth` (notion) and `IGNORE_HEADER_AUTH` (atlassian) in
# connectors.json. Each server's args must bind 0.0.0.0:8080: none of these
# four read $PORT except gworkspace.

locals {
  connectors = jsondecode(file("${path.module}/connectors.json"))
  connector_secrets = merge([
    for name, cfg in local.connectors : {
      for env_var in cfg.secret_env : "${name}-${env_var}" => {
        connector = name
        env_var   = env_var
        secret_id = lower(replace("mcp-${name}-${env_var}", "_", "-"))
      }
    }
  ]...)
  connector_env_grants = merge([
    for name, cfg in local.connectors : {
      for env in keys(local.environments) : "${name}-${env}" => {
        connector   = name
        environment = env
      }
    }
  ]...)
}

resource "google_service_account" "connector" {
  for_each     = local.connectors
  account_id   = "naxos2-mcp-${each.key}"
  display_name = "naxos MCP connector: ${each.key}"
}

# Values are set out of band (gcloud/CI owns secret values); Terraform owns the
# shell and the IAM.
resource "google_secret_manager_secret" "connector" {
  for_each  = local.connector_secrets
  secret_id = each.value.secret_id
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled]
}

# A Cloud Run revision mounting `version = "latest"` cannot be created while the
# secret has no versions, so apply would fail before anyone could set the real
# value. This placeholder makes the service deployable; the connector fails to
# authenticate until `gcloud secrets versions add` supplies the real token, and
# ignore_changes keeps Terraform from reverting to the placeholder afterwards.
resource "google_secret_manager_secret_version" "connector_placeholder" {
  for_each    = local.connector_secrets
  secret      = google_secret_manager_secret.connector[each.key].id
  secret_data = "unset"

  lifecycle {
    ignore_changes = [secret_data]
  }
}

resource "google_secret_manager_secret_iam_member" "connector" {
  for_each  = local.connector_secrets
  secret_id = google_secret_manager_secret.connector[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.connector[each.value.connector].email}"
}

resource "google_cloud_run_v2_service" "connector" {
  for_each = local.connectors
  name     = "naxos-mcp-${each.key}"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.connector[each.key].email
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = local.placeholder_image
      args  = each.value.args
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
      dynamic "env" {
        for_each = each.value.env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.value.secret_env
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.connector["${each.key}-${env.value}"].secret_id
              version = "latest"
            }
          }
        }
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

  # The revision is validated against the secrets at creation, so both the
  # placeholder version and the SA's accessor grant must already exist.
  depends_on = [
    google_secret_manager_secret_version.connector_placeholder,
    google_secret_manager_secret_iam_member.connector,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "connector_env_invoker" {
  for_each = local.connector_env_grants
  name     = google_cloud_run_v2_service.connector[each.value.connector].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.environment[each.value.environment].email}"
}

output "connector_urls" {
  value = { for name, service in google_cloud_run_v2_service.connector : name => service.uri }
}
