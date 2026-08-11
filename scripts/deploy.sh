#!/usr/bin/env bash
# Build all three images with Cloud Build and roll them onto Cloud Run.
set -euo pipefail

PROJECT=${PROJECT:-naxos-503510}
REGION=${REGION:-asia-northeast1}
REPO="$REGION-docker.pkg.dev/$PROJECT/naxos"
TAG=${TAG:-$(git rev-parse --short HEAD)}

# Stage in the Terraform-owned bucket: the default path verifies bucket
# ownership via a project-level storage.buckets.list, which the CI deployer SA
# does not have, and the default _cloudbuild bucket's ACLs are unmanageable.
# Until `terraform apply` has created that bucket, fall back to the default
# _cloudbuild bucket, which the deployer SA can already write to.
STAGING="gs://$PROJECT-build-staging"
if ! gcloud storage buckets describe "$STAGING" >/dev/null 2>&1; then
  echo "$STAGING not accessible (terraform not applied yet?); staging in ${PROJECT}_cloudbuild"
  STAGING="gs://${PROJECT}_cloudbuild"
fi

gcloud builds submit --project "$PROJECT" --config cloudbuild.yaml \
  --gcs-source-staging-dir "$STAGING/source" \
  --substitutions "_REPO=$REPO,_TAG=$TAG" .

# Listed before forking: a command substitution in a for-loop word list would
# not trip errexit, silently skipping every sandbox job rollout.
sbx_jobs=$(gcloud run jobs list --project "$PROJECT" --region "$REGION" \
  --format "value(metadata.name)" --filter "metadata.name~naxos-sbx-")

# Warm the credential cache once: concurrent gcloud processes refreshing the
# shared sqlite token cache can hit "database is locked".
gcloud auth print-access-token --project "$PROJECT" >/dev/null

# Rollouts are independent; run them concurrently, wait on every pid (bare
# `wait` would swallow statuses, and errexit on the first failed wait would
# abandon the still-running ones unreported), and fail if any failed.
pids=()
gcloud run services update naxos-api --project "$PROJECT" --region "$REGION" \
  --image "$REPO/control-plane:$TAG" --args api &
pids+=($!)
gcloud run services update naxos-internal --project "$PROJECT" --region "$REGION" \
  --image "$REPO/control-plane:$TAG" --args internal &
pids+=($!)
gcloud run services update naxos-egress --project "$PROJECT" --region "$REGION" \
  --image "$REPO/egress-proxy:$TAG" &
pids+=($!)

for job in $sbx_jobs; do
  gcloud run jobs update "$job" --project "$PROJECT" --region "$REGION" \
    --image "$REPO/sandbox-runner:$TAG" &
  pids+=($!)
done

status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
[ "$status" -eq 0 ] || { echo "one or more rollouts failed" >&2; exit 1; }

echo "deployed $TAG"
