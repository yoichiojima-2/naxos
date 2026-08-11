#!/usr/bin/env bash
# Mirror the upstream MCP connector images into Artifact Registry and roll them
# onto their Cloud Run services. Cloud Run can only pull from Artifact Registry,
# and Terraform owns service shells rather than images, so this is the image
# half of `terraform apply` for connectors.
#
# Images are unmodified upstream builds; pinning a digest here is how you
# control which upstream version runs. Run after `terraform apply` and after
# setting the connector secrets:
#   gcloud secrets versions add mcp-slack-slack-mcp-xoxp-token --data-file=-
set -euo pipefail

PROJECT=${PROJECT:-naxos-503510}
REGION=${REGION:-asia-northeast1}
REPO="$REGION-docker.pkg.dev/$PROJECT/naxos"
CONNECTORS_JSON=${CONNECTORS_JSON:-terraform/connectors.json}

names=$(jq -r 'keys[]' "$CONNECTORS_JSON")
[ -n "${1:-}" ] && names="$*"

for name in $names; do
  upstream=$(jq -r --arg n "$name" '.[$n].upstream_image' "$CONNECTORS_JSON")
  if [ "$upstream" = "null" ]; then
    echo "unknown connector: $name" >&2
    exit 1
  fi
  target="$REPO/mcp-$name:latest"
  echo "mirroring $upstream -> $target"
  docker pull "$upstream"
  docker tag "$upstream" "$target"
  docker push "$target"
  gcloud run services update "naxos-mcp-$name" --project "$PROJECT" --region "$REGION" \
    --image "$target"
done

echo "mirrored: $names"
