#!/usr/bin/env bash
# Build all three images with Cloud Build and roll them onto Cloud Run.
set -euo pipefail

PROJECT=${PROJECT:-naxos-503510}
REGION=${REGION:-asia-northeast1}
REPO="$REGION-docker.pkg.dev/$PROJECT/naxos"
TAG=${TAG:-$(git rev-parse --short HEAD)}

# Explicit staging dir: the default path verifies bucket ownership via a
# project-level storage.buckets.list, which the CI deployer SA does not have.
gcloud builds submit --project "$PROJECT" --config cloudbuild.yaml \
  --gcs-source-staging-dir "gs://${PROJECT}_cloudbuild/source" \
  --substitutions "_REPO=$REPO,_TAG=$TAG" .

gcloud run services update naxos-api --project "$PROJECT" --region "$REGION" \
  --image "$REPO/control-plane:$TAG" --args api
gcloud run services update naxos-internal --project "$PROJECT" --region "$REGION" \
  --image "$REPO/control-plane:$TAG" --args internal
gcloud run services update naxos-egress --project "$PROJECT" --region "$REGION" \
  --image "$REPO/egress-proxy:$TAG"

for job in $(gcloud run jobs list --project "$PROJECT" --region "$REGION" \
    --format "value(metadata.name)" --filter "metadata.name~naxos-sbx-"); do
  gcloud run jobs update "$job" --project "$PROJECT" --region "$REGION" \
    --image "$REPO/sandbox-runner:$TAG"
done

echo "deployed $TAG"
