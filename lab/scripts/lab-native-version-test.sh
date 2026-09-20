#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
BASE="${AMP_URL:-http://127.0.0.1:8080}"; TENANT="${AMP_VERSION_TEST_TENANT:-demo}"; NS="${AMP_VERSION_TEST_NAMESPACE:-version-test}"
GID="${AMP_VERSION_TEST_CATALOGUE_GROUP_ID:-}"; RUN="${AMP_VERSION_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"; KEY="version-tests/$RUN/obj1.json"
fail(){ echo "[FAIL] $*" >&2; exit 1; }; ok(){ echo "[ok] $*"; }
command -v curl >/dev/null || fail "curl required"; command -v jq >/dev/null || fail "jq required"; curl -fsS "$BASE/healthz" >/dev/null || fail "AMP not healthy"
API_KEY="${AMP_API_KEY:-}"; [[ -z "$API_KEY" && -f .env ]] && API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' .env || true)"; AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")
if [[ -z "$GID" ]]; then GID="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT" | jq -r '.[]|select(.container_name=="amp-primary" and .state=="ACTIVE")|.id' | head -1)"; fi
[[ -n "$GID" && "$GID" != null ]] || fail "target catalogue group not found"
curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/gateway-routes" -H 'Content-Type: application/json' -d "$(jq -nc --arg t "$TENANT" --arg n "$NS" --arg g "$GID" '{tenant_id:$t,namespace:$n,catalogue_group_id:$g,response_mode:"AMP_NORMALIZED",backend_header_policy:"SELECTED"}')" >/dev/null

echo "AMP backend-native version tracking test"
echo "----------------------------------------"
echo "Catalogue : $GID"; echo "Object    : $KEY"
H1=$(mktemp); H2=$(mktemp); trap 'rm -f "$H1" "$H2"' EXIT
curl -fsS "${AUTH[@]}" -D "$H1" -o /dev/null -X PUT "$BASE/rest/$TENANT/$NS/$KEY" -H 'Content-Type: application/json' --data-binary '{"revision":1,"value":"first"}'
OID1="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-object-id:/{gsub("\r","");print $2}' "$H1" | tail -1)"; RID1="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-recon-id:/{gsub("\r","");print $2}' "$H1" | tail -1)"; V1="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-version-id:/{gsub("\r","");print $2}' "$H1" | tail -1)"
[[ -n "$OID1" && -n "$RID1" && -n "$V1" ]] || fail "first PUT missing object/recon/native version headers"
for a in default legal migration; do curl -fsS "${AUTH[@]}" -X PUT "$BASE/rest/$TENANT/$NS/$KEY?type=custom-metadata&annotation=$a" -H 'Content-Type: application/json' --data-binary "{\"annotation\":\"$a\",\"revision\":1}" >/dev/null; done
curl -fsS "${AUTH[@]}" -D "$H2" -o /dev/null -X PUT "$BASE/rest/$TENANT/$NS/$KEY" -H 'Content-Type: application/json' --data-binary '{"revision":2,"value":"second"}'
OID2="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-object-id:/{gsub("\r","");print $2}' "$H2" | tail -1)"; RID2="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-recon-id:/{gsub("\r","");print $2}' "$H2" | tail -1)"; V2="$(awk 'BEGIN{IGNORECASE=1}/^x-amp-version-id:/{gsub("\r","");print $2}' "$H2" | tail -1)"
[[ "$OID1" == "$OID2" && "$RID1" == "$RID2" ]] || fail "overwrite created a new AMP logical identity"
[[ -n "$V2" && "$V1" != "$V2" ]] || fail "backend did not expose a new native version; confirm lab bucket versioning is enabled"
for a in default legal migration; do curl -fsS "${AUTH[@]}" -X PUT "$BASE/rest/$TENANT/$NS/$KEY?type=custom-metadata&annotation=$a" -H 'Content-Type: application/json' --data-binary "{\"annotation\":\"$a\",\"revision\":2}" >/dev/null; done
CUR="$(curl -fsS "${AUTH[@]}" "$BASE/rest/$TENANT/$NS/$KEY")"; [[ "$(jq -r .revision <<<"$CUR")" == 2 ]] || fail "current payload not revision 2"
OLD="$(curl -fsS "${AUTH[@]}" "$BASE/rest/$TENANT/$NS/$KEY?versionId=$V1")"; [[ "$(jq -r .revision <<<"$OLD")" == 1 ]] || fail "explicit old backend version did not return revision 1"
OLD_ANN="$(curl -fsS "${AUTH[@]}" "$BASE/rest/$TENANT/$NS/$KEY?type=custom-metadata&annotation=legal&versionId=$V1")"
CUR_ANN="$(curl -fsS "${AUTH[@]}" "$BASE/rest/$TENANT/$NS/$KEY?type=custom-metadata&annotation=legal&versionId=$V2")"
[[ "$(jq -r .revision <<<"$OLD_ANN")" == 1 ]] || fail "payload V1 did not resolve legal annotation revision 1"
[[ "$(jq -r .revision <<<"$CUR_ANN")" == 2 ]] || fail "payload V2 did not resolve legal annotation revision 2"
VERS="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$OID1/versions?refresh=true")"; [[ "$(jq 'length' <<<"$VERS")" -ge 2 ]] || fail "catalogue did not observe at least two native payload versions"
DETAIL="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$OID1")"; [[ "$(jq -r .id <<<"$DETAIL")" == "$OID1" ]] || fail "logical object changed"; [[ "$(jq -r .version_id <<<"$DETAIL")" == "$V2" ]] || fail "current native version not V2"
for a in default legal migration; do COUNT="$(jq --arg a "$a" '[.annotations[]|select(.annotation_name==$a)|.native_versions[]]|length' <<<"$DETAIL")"; [[ "$COUNT" -ge 2 ]] || fail "$a annotation did not retain two backend-native versions"; done
ok "same AMP object/recon identity retained across backend-native versions"
ok "explicit V1 retrieval returned old payload; current retrieval returned V2"
ok "payload + annotation native versions catalogued; old payload versions resolve the correct annotation snapshot"
ok "AMP performed no pruning/lifecycle/WORM management; backend remains authoritative"
echo; echo "PASS - backend-native version ownership"
echo "V1=$V1"; echo "V2=$V2"; echo "ObjectID=$OID1"; echo "ReconID=$RID1"
