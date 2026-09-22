#!/usr/bin/env bash
set -euo pipefail
BASE="${AMP_URL:-http://localhost:8080}"
need(){ command -v "$1" >/dev/null || { echo "missing command: $1"; exit 1; }; }
need curl; need python3

echo "==> health"
curl -fsS "$BASE/healthz" | python3 -m json.tool

echo "==> overview"
curl -fsS "$BASE/api/v1/overview" | python3 -m json.tool

echo "==> catalogue groups"
CATALOGUE_GROUPS_JSON="$(curl -fsS "$BASE/api/v1/catalogue-groups")"
echo "$CATALOGUE_GROUPS_JSON" | python3 -m json.tool
GID="$(python3 -c 'import json,sys; x=json.load(sys.stdin); print(next((g["id"] for g in x if "Legacy" in g.get("name","")), x[0]["id"] if x else ""))' <<<"$CATALOGUE_GROUPS_JSON")"
[[ -n "$GID" ]] || { echo "no catalogue group found"; exit 1; }

echo "==> search"
curl -fsS "$BASE/api/v1/search?q=convenience" | python3 -m json.tool

echo "==> reconciliation for $GID"
curl -fsS -X POST "$BASE/api/v1/reconciliation/run" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"demo\",\"catalogue_group_id\":\"$GID\",\"target\":\"ALL\"}" | python3 -m json.tool

echo "[ok] AMP container-sharded catalogue smoke test passed"
