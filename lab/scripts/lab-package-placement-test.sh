#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_PACKAGE_TEST_TENANT:-demo}"
NAMESPACE="${AMP_PACKAGE_TEST_NAMESPACE:-package-placement-test}"
GID="${AMP_PACKAGE_TEST_CATALOGUE_GROUP_ID:-}"
RUN_ID="${AMP_PACKAGE_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
fail(){ echo "[FAIL] $*" >&2; exit 1; }
ok(){ echo "[ok] $*"; }
command -v curl >/dev/null || fail "curl required"
command -v jq >/dev/null || fail "jq required"
API_KEY="${AMP_API_KEY:-}"
if [[ -z "$API_KEY" && -f .env ]]; then API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' .env || true)"; fi
AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")
curl -fsS "$BASE/healthz" >/dev/null || fail "AMP API is not healthy"
if [[ -z "$GID" ]]; then
  GID="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT" | jq -r '.[]|select(.state=="ACTIVE")|.id' | head -1)"
fi
[[ -n "$GID" && "$GID" != null ]] || fail "No active catalogue group"
ORIGINAL="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-groups/$GID/package-layout")"
curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/gateway-routes" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$TENANT" --arg n "$NAMESPACE" --arg g "$GID" '{tenant_id:$t,namespace:$n,catalogue_group_id:$g,object_prefix:""}')" >/dev/null
restore(){
  curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/catalogue-groups/$GID/package-layout" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg m "$(jq -r .mode <<<"$ORIGINAL")" --arg r "$(jq -r .root_prefix <<<"$ORIGINAL")" --argjson l "$(jq -r .hash_levels <<<"$ORIGINAL")" --argjson w "$(jq -r .hash_segment_chars <<<"$ORIGINAL")" '{mode:$m,root_prefix:$r,hash_levels:$l,hash_segment_chars:$w}')" >/dev/null || true
}
trap restore EXIT

echo "AMP package placement integration test"
echo "--------------------------------------"
echo "Catalogue group : $GID"
echo "Namespace       : $TENANT/$NAMESPACE"

# Managed hash mode
curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/catalogue-groups/$GID/package-layout" -H 'Content-Type: application/json' \
  -d '{"mode":"AMP_MANAGED_HASH","root_prefix":".amp/objects","hash_levels":2,"hash_segment_chars":2}' >/dev/null
KEY1="placement-tests/${RUN_ID}/managed/object.json"
H1="$(mktemp)"; trap 'rm -f "$H1"; restore' EXIT
curl -fsS "${AUTH[@]}" -D "$H1" -o /dev/null -X PUT "$BASE/rest/$TENANT/$NAMESPACE/$KEY1" -H 'Content-Type: application/json' --data-binary '{"mode":"managed"}'
OID1="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-object-id:/{gsub("\\r","");print $2}' "$H1" | tail -1)"
D1="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$OID1")"
R1="$(jq -r .package_root <<<"$D1")"
[[ "$R1" =~ ^\.amp/objects/[0-9a-fA-F]{2}/[0-9a-fA-F]{2}/[0-9a-fA-F-]{36}$ ]] || fail "Managed root invalid: $R1"
[[ "$(jq -r .payload_key <<<"$D1")" == "$R1/payload" ]] || fail "Managed payload key invalid"
[[ "$(jq -r .manifest_key <<<"$D1")" == "$R1/manifest.json" ]] || fail "Managed manifest key invalid"
ok "AMP managed hash distribution -> $R1"

# Client path mode
curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/catalogue-groups/$GID/package-layout" -H 'Content-Type: application/json' \
  -d '{"mode":"CLIENT_PATH","root_prefix":".amp/objects","hash_levels":2,"hash_segment_chars":2}' >/dev/null
KEY2="placement-tests/${RUN_ID}/client/contracts/agreement.pdf"
H2="$(mktemp)"; curl -fsS "${AUTH[@]}" -D "$H2" -o /dev/null -X PUT "$BASE/rest/$TENANT/$NAMESPACE/$KEY2" -H 'Content-Type: application/pdf' --data-binary 'client-path-payload'
OID2="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-object-id:/{gsub("\\r","");print $2}' "$H2" | tail -1)"; rm -f "$H2"
D2="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$OID2")"
R2="$(jq -r .package_root <<<"$D2")"
[[ "$R2" == "$KEY2/"* ]] || fail "Client-path root invalid: $R2"
PID2="${R2##*/}"; [[ ${#PID2} -eq 36 ]] || fail "Client-path GUID invalid: $PID2"
[[ "$(jq -r .payload_key <<<"$D2")" == "$R2/payload" ]] || fail "Client-path payload key invalid"
[[ "$(jq -r .manifest_key <<<"$D2")" == "$R2/manifest.json" ]] || fail "Client-path manifest key invalid"
curl -fsS "${AUTH[@]}" -X PUT "$BASE/rest/$TENANT/$NAMESPACE/$KEY2?type=custom-metadata&annotation=legal" -H 'Content-Type: application/json' --data-binary '{"retentionYears":10}' >/dev/null
D2="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$OID2")"
[[ "$(jq -r '.annotations[]|select(.annotation_name=="legal")|.sidecar_key' <<<"$D2")" == "$R2/annotations/legal.json" ]] || fail "Client-path annotation path invalid"
ok "Client REST/S3 path preserved -> $R2"

echo
echo "PASS - configurable AMP package placement"
echo "------------------------------------------"
echo "AMP managed   : $R1/{payload,annotations,manifest.json}"
echo "Client path   : $R2/{payload,annotations,manifest.json}"
echo "Package format: AMP_PACKAGE_V3"
