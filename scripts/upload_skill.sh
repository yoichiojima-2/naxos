#!/usr/bin/env bash
# Upload a local skill folder (SKILL.md + supporting files) to the control plane.
#
#   NAXOS_API=https://<naxos-api-url> scripts/upload_skill.sh docs/skills/bigquery [name]
#
# Creates the skill if no active skill has that name, otherwise upserts files
# into the existing one. NAXOS_AUTH, if set, is passed as an extra header
# (e.g. 'Proxy-Authorization: Bearer ...'); a dev-mode control plane needs none.
set -euo pipefail

DIR=${1:?usage: NAXOS_API=<url> upload_skill.sh <skill-dir> [name]}
NAME=${2:-$(basename "$DIR")}
API=${NAXOS_API:?set NAXOS_API to the control plane base URL}

[ -f "$DIR/SKILL.md" ] || { echo "$DIR has no SKILL.md" >&2; exit 1; }

req() {
  curl -sS --fail-with-body ${NAXOS_AUTH:+-H "$NAXOS_AUTH"} -H "Content-Type: application/json" "$@"
}

# Single-line frontmatter value; the API stores it as the catalog description.
DESC=$(sed -n 's/^description: *//p' "$DIR/SKILL.md" | head -1)

SKILL_ID=$(req "$API/v1/skills" | jq -r --arg n "$NAME" '.data[] | select(.name == $n) | .id')
if [ -z "$SKILL_ID" ]; then
  SKILL_ID=$(jq -n --arg n "$NAME" --arg d "$DESC" '{name: $n, description: $d}' \
    | req -X POST "$API/v1/skills" -d @- | jq -r .id)
  echo "created skill $NAME ($SKILL_ID)"
else
  echo "updating skill $NAME ($SKILL_ID)"
fi

find "$DIR" -type f | sort | while read -r file; do
  rel=${file#"$DIR"/}
  jq -n --arg p "$rel" --rawfile c "$file" '{path: $p, content: $c}' \
    | req -X POST "$API/v1/skills/$SKILL_ID/files" -d @- > /dev/null
  echo "  $rel"
done

echo "done — attach \"$SKILL_ID\" via agent_versions.skill_ids"
