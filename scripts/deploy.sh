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

# Rollouts are independent; run them concurrently and wait on each pid so a
# failed update still fails the deploy (bare `wait` would swallow the status).
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

for job in $(gcloud run jobs list --project "$PROJECT" --region "$REGION" \
    --format "value(metadata.name)" --filter "metadata.name~naxos-sbx-"); do
  gcloud run jobs update "$job" --project "$PROJECT" --region "$REGION" \
    --image "$REPO/sandbox-runner:$TAG" &
  pids+=($!)
done

for pid in "${pids[@]}"; do
  wait "$pid"
done

echo "deployed $TAG"
