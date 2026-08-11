#!/usr/bin/env bash
# Remove the abandoned v1 (poc) generation of naxos from the GCP project.
#
# v1 was left in place when main was reset to the v2 scaffold (commit 21af54e;
# v2 renamed its own resources to naxos2-* / naxos_audit to avoid colliding
# with it). Everything below is v1-only and unused by the current stack.
#
# Kept (used by v2 — never touched here):
#   bucket gs://naxos-503510            terraform state backend (prefix terraform/v2)
#   secrets anthropic-api-key, database-url
#   BigQuery dataset naxos_audit
#   Cloud Run naxos-api / naxos-internal / naxos-egress / naxos-sbx-*
#   SAs naxos2-*, WIF pool naxos-github, Artifact Registry repo "naxos"
#   Scheduler jobs naxos-reconcile and naxos-deploy-*
#
# Default is a dry run that only reports what exists; pass --delete to remove.
set -uo pipefail

PROJECT=${PROJECT:-naxos-503510}
REGION=${REGION:-asia-northeast1}
DELETE=0
[ "${1:-}" = "--delete" ] && DELETE=1

found=0
run() {
  # run "description" cmd...
  local desc=$1
  shift
  found=1
  if [ "$DELETE" = 1 ]; then
    echo "DELETE  $desc"
    "$@" || echo "  ^ failed (may need extra permissions or manual cleanup)"
  else
    echo "WOULD DELETE  $desc"
  fi
}

echo "project=$PROJECT region=$REGION mode=$([ "$DELETE" = 1 ] && echo DELETE || echo dry-run)"
echo

# --- Cloud Run (v1 UI service and per-role runner jobs) ----------------------

if gcloud run services describe naxos-ui --project "$PROJECT" --region "$REGION" >/dev/null 2>&1; then
  run "Cloud Run service naxos-ui" \
    gcloud run services delete naxos-ui --project "$PROJECT" --region "$REGION" --quiet
fi

for job in $(gcloud run jobs list --project "$PROJECT" --region "$REGION" \
    --format "value(metadata.name)" --filter "metadata.name~^naxos-runner-" 2>/dev/null); do
  run "Cloud Run job $job" \
    gcloud run jobs delete "$job" --project "$PROJECT" --region "$REGION" --quiet
done

# --- Scheduler (v1 user-created schedules; keep naxos-reconcile / naxos-deploy-*) ---

for sched in $(gcloud scheduler jobs list --project "$PROJECT" --location "$REGION" \
    --format "value(name.basename())" --filter "name~naxos-schedule-" 2>/dev/null); do
  run "Scheduler job $sched" \
    gcloud scheduler jobs delete "$sched" --project "$PROJECT" --location "$REGION" --quiet
done

# --- Service accounts (v1: sa-role-{role}, sa-naxos-ui, sa-deployer, sa-scheduler) ---

for sa in sa-role-ops sa-role-analyst sa-naxos-ui sa-deployer sa-scheduler; do
  email="$sa@$PROJECT.iam.gserviceaccount.com"
  if gcloud iam service-accounts describe "$email" --project "$PROJECT" >/dev/null 2>&1; then
    run "service account $email" \
      gcloud iam service-accounts delete "$email" --project "$PROJECT" --quiet
  fi
done

# --- BigQuery (v1 dataset "audit"; v2 uses naxos_audit) ----------------------

if bq show --project_id="$PROJECT" audit >/dev/null 2>&1; then
  run "BigQuery dataset audit (v2 uses naxos_audit)" \
    bq rm -r -f --project_id="$PROJECT" audit
fi

# --- Storage -----------------------------------------------------------------
# gs://naxos-503510 itself stays (v2 terraform state backend); only the v1
# state prefix inside it goes.

if gcloud storage ls "gs://$PROJECT/terraform/state" >/dev/null 2>&1; then
  run "v1 terraform state gs://$PROJECT/terraform/state/**" \
    gcloud storage rm -r "gs://$PROJECT/terraform/state"
fi

for bucket in "$PROJECT-sessions-ops" "$PROJECT-sessions-analyst" "$PROJECT-artifacts"; do
  if gcloud storage buckets describe "gs://$bucket" >/dev/null 2>&1; then
    run "bucket gs://$bucket (and contents)" \
      gcloud storage rm -r "gs://$bucket"
  fi
done

# --- Artifact Registry (v1 images from `gcloud run deploy --source`) ---------

if gcloud artifacts repositories describe cloud-run-source-deploy \
    --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
  run "Artifact Registry repo cloud-run-source-deploy" \
    gcloud artifacts repositories delete cloud-run-source-deploy \
      --project "$PROJECT" --location "$REGION" --quiet
fi

# --- Secrets (v1-only; anthropic-api-key is shared with v2 and stays) --------

if gcloud secrets describe slack-webhook-url --project "$PROJECT" >/dev/null 2>&1; then
  run "secret slack-webhook-url" \
    gcloud secrets delete slack-webhook-url --project "$PROJECT" --quiet
fi

# --- IAM: custom role + v1 WIF pool (v2 uses naxos-github) -------------------

if gcloud iam roles describe naxosSchedulerEditor --project "$PROJECT" >/dev/null 2>&1; then
  run "custom role naxosSchedulerEditor" \
    gcloud iam roles delete naxosSchedulerEditor --project "$PROJECT" --quiet
fi

if gcloud iam workload-identity-pools describe github --project "$PROJECT" \
    --location global >/dev/null 2>&1; then
  run "workload identity pool github (soft-deleted 30 days)" \
    gcloud iam workload-identity-pools delete github --project "$PROJECT" \
      --location global --quiet
fi

# --- Report-only: things that need a human decision --------------------------

echo
echo "Check manually (not deleted by this script):"
echo "  - Billing budget 'naxos monthly' (v1, forecast/2000 JPY): v2 tfvars has no"
echo "    billing_account, so this v1 budget is the only spend alert. Keep it, or"
echo "    set billing_account in terraform.tfvars and apply before removing it:"
echo "      gcloud billing budgets list --billing-account=<ACCOUNT_ID>"
echo "  - Early v2 VPC remnants, if a pre-887b9ac apply ever created them:"
for net in naxos-vpc; do
  gcloud compute networks describe "$net" --project "$PROJECT" >/dev/null 2>&1 \
    && echo "      FOUND: network $net (with subnet naxos-run / address naxos-private-ip)" \
    || echo "      network $net: not present (nothing to do)"
done
echo "  - Stale project-IAM bindings for deleted SAs (deleted:serviceAccount:...):"
echo "      gcloud projects get-iam-policy $PROJECT --format=json | grep deleted:"

echo
if [ "$found" = 0 ]; then
  echo "nothing to clean — no v1 resources found"
elif [ "$DELETE" = 0 ]; then
  echo "dry run only — rerun with --delete to remove the resources listed above"
fi
