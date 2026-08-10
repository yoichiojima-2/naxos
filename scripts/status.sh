#!/usr/bin/env bash
# One-shot health/cost view of the naxos deployment.
set -euo pipefail

PROJECT=${PROJECT:-naxos-503510}
REGION=${REGION:-asia-northeast1}

section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

section "Cloud Run services"
gcloud run services list --project "$PROJECT" --region "$REGION" \
  --format="table(name, status.url, status.latestReadyRevisionName)"

section "Sandbox executions (last 5)"
gcloud run jobs executions list --job naxos-sbx-default --project "$PROJECT" --region "$REGION" \
  --limit 5 --format="table(name, creationTimestamp.date('%m-%d %H:%M'), status.completionTime.date('%m-%d %H:%M'), status.succeededCount, status.failedCount)" \
  2>/dev/null || echo "no executions yet"

section "Cloud SQL"
STATE=$(gcloud sql instances describe naxos-state --project "$PROJECT" \
  --format="value(state, settings.activationPolicy, settings.tier)")
echo "$STATE"
case "$STATE" in
  *NEVER*) echo "(parked — ≈¥100–200/mo; wake: gcloud sql instances patch naxos-state --activation-policy ALWAYS)" ;;
  *ALWAYS*) echo "(running — ≈¥1,500/mo; park: gcloud sql instances patch naxos-state --activation-policy NEVER)" ;;
esac

section "Scheduler"
gcloud scheduler jobs list --project "$PROJECT" --location "$REGION" \
  --format="table(name.basename(), schedule, state)"

section "Recent runs (naxos_audit.runs)"
bq query --project_id="$PROJECT" --use_legacy_sql=false --format=pretty --quiet \
  'SELECT FORMAT_TIMESTAMP("%m-%d %H:%M", started_at) AS started, session_id, trigger_type,
          stop_reason, num_turns, ROUND(cost_usd, 4) AS usd
   FROM naxos_audit.runs ORDER BY started_at DESC LIMIT 5' 2>/dev/null \
  || echo "no audit rows (or Cloud SQL parked and nothing has run)"

section "Recent tool calls (naxos_audit.tool_calls)"
bq query --project_id="$PROJECT" --use_legacy_sql=false --format=pretty --quiet \
  'SELECT FORMAT_TIMESTAMP("%m-%d %H:%M", ts) AS called_at, session_id, tool_name, decision
   FROM naxos_audit.tool_calls ORDER BY ts DESC LIMIT 5' 2>/dev/null || echo "no tool calls"

section "Recent errors (api + internal, last hour)"
gcloud logging read \
  'resource.type=cloud_run_revision severity>=WARNING
   resource.labels.service_name=("naxos-api" OR "naxos-internal" OR "naxos-egress")' \
  --project "$PROJECT" --freshness=1h --limit 5 \
  --format="table(timestamp.date('%H:%M:%S'), resource.labels.service_name, textPayload.slice(0:120))" \
  2>/dev/null | head -10 || true
echo
echo "billing: https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT"
