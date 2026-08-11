#!/usr/bin/env bash
# Upload a local skill folder (SKILL.md + supporting files) to the control plane.
#
#   NAXOS_API=https://<naxos-api-url> scripts/upload_skill.sh docs/skills/bigquery [name]
#
# Creates the skill if no active skill has that name, otherwise syncs it:
# local files are upserted, remote files no longer present locally are
# deleted. Dotfiles are ignored; non-text files are rejected (the API stores
# content as text). The catalog description is taken from the SKILL.md
# frontmatter at creation only — the API has no skill-metadata update, so
# later frontmatter edits show up in the mounted SKILL.md but not the catalog.
# NAXOS_AUTH, if set, is passed as an extra header (e.g. 'Proxy-Authorization:
# Bearer ...'); a dev-mode control plane needs none.
set -euo pipefail

DIR=${1:?usage: NAXOS_API=<url> upload_skill.sh <skill-dir> [name]}
NAME=${2:-$(basename "$DIR")}
API=${NAXOS_API:?set NAXOS_API to the control plane base URL}

[ -f "$DIR/SKILL.md" ] || { echo "$DIR has no SKILL.md" >&2; exit 1; }

mapfile -t FILES < <(find "$DIR" -type f ! -path '*/.*' | sort)
for file in "${FILES[@]}"; do
  [ ! -s "$file" ] || grep -Iq . "$file" \
    || { echo "$file is not text; skill files are stored as text" >&2; exit 1; }
done

req() {
  curl -sS --fail-with-body ${NAXOS_AUTH:+-H "$NAXOS_AUTH"} -H "Content-Type: application/json" "$@"
}

SKILL_ID=$(req "$API/v1/skills" | jq -r --arg n "$NAME" '.data[] | select(.name == $n) | .id')
if [ -z "$SKILL_ID" ]; then
  # Single-line frontmatter value; the API stores it as the catalog description.
  DESC=$(sed -n 's/^description: *//p' "$DIR/SKILL.md" | head -1)
  SKILL_ID=$(jq -n --arg n "$NAME" --arg d "$DESC" '{name: $n, description: $d}' \
    | req -X POST "$API/v1/skills" -d @- | jq -r .id)
  echo "created skill $NAME ($SKILL_ID)"
else
  echo "updating skill $NAME ($SKILL_ID)"
fi

for file in "${FILES[@]}"; do
  rel=${file#"$DIR"/}
  jq -n --arg p "$rel" --rawfile c "$file" '{path: $p, content: $c}' \
    | req -X POST "$API/v1/skills/$SKILL_ID/files" -d @- > /dev/null
  echo "  $rel"
done

req "$API/v1/skills/$SKILL_ID/files" | jq -r '.data[] | [.id, .path] | @tsv' \
  | while IFS=$'\t' read -r fid rel; do
      printf '%s\n' "${FILES[@]#"$DIR"/}" | grep -Fxq "$rel" && continue
      req -X DELETE "$API/v1/skills/$SKILL_ID/files/$fid" > /dev/null
      echo "  deleted $rel"
    done

echo "done — attach \"$SKILL_ID\" via agent_versions.skill_ids"
