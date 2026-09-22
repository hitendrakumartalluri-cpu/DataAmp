#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_RECON_TEST_TENANT:-demo}"
NAMESPACE="${AMP_RECON_TEST_NAMESPACE:-recon-test}"
TARGET_CONTAINER="${AMP_RECON_TEST_CONTAINER:-amp-primary}"
RUN_ID="${AMP_RECON_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
PREFIX="${AMP_RECON_TEST_PREFIX:-reconciliation-tests/$RUN_ID/}"
GID="${AMP_RECON_TEST_CATALOGUE_GROUP_ID:-}"

fail(){ echo "[FAIL] $*" >&2; exit 1; }
ok(){ echo "[ok] $*"; }
info(){ echo "==> $*"; }

command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
curl -fsS "$BASE/healthz" >/dev/null || fail "AMP API is not healthy at $BASE"

API_KEY="${AMP_API_KEY:-}"
if [[ -z "$API_KEY" && -f .env ]]; then
  API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' .env || true)"
fi
AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")

if [[ -z "$GID" ]]; then
  CATALOGUE_GROUPS_JSON="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT")"
  GID="$(jq -r --arg c "$TARGET_CONTAINER" '.[]|select(.container_name==$c and .state=="ACTIVE")|.id' <<<"$CATALOGUE_GROUPS_JSON" | head -1)"
fi
[[ -n "$GID" && "$GID" != "null" ]] || fail "could not resolve ACTIVE catalogue group for $TARGET_CONTAINER"

info "Configuring route $TENANT/$NAMESPACE -> $TARGET_CONTAINER"
curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/gateway-routes" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$TENANT" --arg n "$NAMESPACE" --arg g "$GID" '{tenant_id:$t,namespace:$n,catalogue_group_id:$g,object_prefix:""}')" >/dev/null

cat <<EOF
AMP storage reconciliation integration test
-------------------------------------------
Tenant          : $TENANT
Namespace       : $NAMESPACE
Catalogue group : $GID
Prefix          : $PREFIX
Objects         : 4 managed packages
EOF

declare -A PAYLOADS
PAYLOADS[1]='{"id":"recon-1","state":"clean","sequence":1}'
PAYLOADS[2]='{"id":"recon-2","state":"clean","sequence":2}'
PAYLOADS[3]='{"id":"recon-3","state":"clean","sequence":3}'
PAYLOADS[4]='{"id":"recon-4","state":"clean","sequence":4}'

info "Phase 1/6 - create four managed objects"
for i in 1 2 3 4; do
  key="${PREFIX}object-$i.json"
  code="$(curl -sS "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X PUT \
    "$BASE/rest/$TENANT/$NAMESPACE/$key" -H 'Content-Type: application/json' --data-binary "${PAYLOADS[$i]}")"
  [[ "$code" == "201" ]] || fail "object $i PUT returned HTTP $code"
done
code="$(curl -sS "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X PUT \
  "$BASE/rest/$TENANT/$NAMESPACE/${PREFIX}object-3.json?type=custom-metadata&annotation=legal" \
  -H 'Content-Type: application/json' --data-binary '{"classification":"CONFIDENTIAL","legalHold":false}')"
[[ "$code" == "201" ]] || fail "legal annotation PUT returned HTTP $code"
ok "managed objects created"

ROWS="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" \
  --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$GID" \
  --data-urlencode "q=$PREFIX" --data-urlencode "limit=20")"
[[ "$(jq 'length' <<<"$ROWS")" -eq 4 ]] || fail "expected 4 Catalogue rows"

clean_recon(){
  curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/reconciliation/run" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg t "$TENANT" --arg g "$GID" --arg p "$PREFIX" \
      '{tenant_id:$t,catalogue_group_id:$g,target:"STORAGE",prefix:$p,storage_mode:"TARGETED",verify_package_members:true,limit:100}')"
}

info "Phase 2/6 - clean TARGETED reconciliation"
R="$(clean_recon)"
[[ "$(jq '.findings' <<<"$R")" -eq 0 ]] || { jq . <<<"$R"; fail "clean reconciliation produced findings"; }
ok "clean Catalogue -> storage/package verification returned zero findings"

DETAIL1="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$(jq -r --arg k "${PREFIX}object-1.json" '.[]|select(.object_key==$k)|.id' <<<"$ROWS")")"
DETAIL2="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$(jq -r --arg k "${PREFIX}object-2.json" '.[]|select(.object_key==$k)|.id' <<<"$ROWS")")"
DETAIL3="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$(jq -r --arg k "${PREFIX}object-3.json" '.[]|select(.object_key==$k)|.id' <<<"$ROWS")")"
DETAIL4="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$(jq -r --arg k "${PREFIX}object-4.json" '.[]|select(.object_key==$k)|.id' <<<"$ROWS")")"

P1="$(jq -r '.payload_key' <<<"$DETAIL1")"
P2="$(jq -r '.payload_key' <<<"$DETAIL2")"
A3="$(jq -r '.annotations[]|select(.annotation_name=="legal")|.sidecar_key' <<<"$DETAIL3")"
M4="$(jq -r '.manifest_key' <<<"$DETAIL4")"

info "Phase 3/6 - inject controlled physical drift below AMP"
docker compose --env-file .env -f docker-compose.yml exec -T \
  -e RECON_P1="$P1" -e RECON_P2="$P2" -e RECON_A3="$A3" -e RECON_M4="$M4" amp python - <<'PY'
import os,boto3
from botocore.config import Config
s3=boto3.client(
    's3', endpoint_url=os.environ['AMP_LAB_PRIMARY_ENDPOINT'],
    aws_access_key_id=os.environ['AMP_LAB_PRIMARY_ACCESS_KEY'],
    aws_secret_access_key=os.environ['AMP_LAB_PRIMARY_SECRET_KEY'],
    region_name='us-east-1', config=Config(s3={'addressing_style':'path'})
)
bucket=os.environ['AMP_LAB_PRIMARY_BUCKET']
s3.delete_object(Bucket=bucket,Key=os.environ['RECON_P1'])
s3.put_object(Bucket=bucket,Key=os.environ['RECON_P2'],Body=b'physical-drift-payload-that-is-deliberately-longer',ContentType='application/json')
s3.delete_object(Bucket=bucket,Key=os.environ['RECON_A3'])
s3.delete_object(Bucket=bucket,Key=os.environ['RECON_M4'])
PY
ok "deleted payload, changed payload, deleted annotation and deleted manifest without changing logical Catalogue rows"

info "Phase 4/6 - TARGETED reconciliation must detect physical drift"
R="$(clean_recon)"
JOB="$(jq -r '.job_id' <<<"$R")"
F="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/reconciliation/findings" \
  --data-urlencode "tenant_id=$TENANT" --data-urlencode "job_id=$JOB" --data-urlencode "limit=100")"
types="$(jq -r '.[].finding_type' <<<"$F" | sort -u | tr '\n' ' ')"
[[ "$types" == *"MISSING_FROM_STORAGE"* ]] || { jq . <<<"$F"; fail "missing payload drift not detected"; }
[[ "$types" == *"SIZE_MISMATCH"* || "$types" == *"VERSION_DRIFT"* ]] || { jq . <<<"$F"; fail "payload overwrite drift not detected"; }
[[ "$types" == *"ANNOTATION_MISSING"* ]] || { jq . <<<"$F"; fail "annotation drift not detected"; }
[[ "$types" == *"MANIFEST_MISSING"* ]] || { jq . <<<"$F"; fail "manifest drift not detected"; }
ok "TARGETED detected payload, version/size, annotation and manifest drift"

info "Phase 5/6 - TALLY must detect logical inventory count drift"
T="$(curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/reconciliation/run" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$TENANT" --arg g "$GID" --arg p "$PREFIX" \
    '{tenant_id:$t,catalogue_group_id:$g,target:"STORAGE",prefix:$p,storage_mode:"TALLY",verify_package_members:false,limit:100}')")"
[[ "$(jq -r '.finding_counts.COUNT_MISMATCH // 0' <<<"$T")" -ge 1 ]] || { jq . <<<"$T"; fail "TALLY did not detect count mismatch"; }
ok "TALLY detected authoritative inventory/Catalogue count mismatch"

info "Phase 6/6 - restore through Gateway and prove FULL reconciliation returns clean"
for i in 1 2 3 4; do
  key="${PREFIX}object-$i.json"
  code="$(curl -sS "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X PUT \
    "$BASE/rest/$TENANT/$NAMESPACE/$key" -H 'Content-Type: application/json' --data-binary "${PAYLOADS[$i]}")"
  [[ "$code" == "201" ]] || fail "restore object $i returned HTTP $code"
done
code="$(curl -sS "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X PUT \
  "$BASE/rest/$TENANT/$NAMESPACE/${PREFIX}object-3.json?type=custom-metadata&annotation=legal" \
  -H 'Content-Type: application/json' --data-binary '{"classification":"CONFIDENTIAL","legalHold":false}')"
[[ "$code" == "201" ]] || fail "restore legal annotation returned HTTP $code"

FULL="$(curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/reconciliation/run" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$TENANT" --arg g "$GID" --arg p "$PREFIX" \
    '{tenant_id:$t,catalogue_group_id:$g,target:"STORAGE",prefix:$p,storage_mode:"FULL",verify_package_members:true,limit:100}')")"
[[ "$(jq '.findings' <<<"$FULL")" -eq 0 ]] || { jq . <<<"$FULL"; fail "restored FULL reconciliation produced findings"; }
ok "FULL bidirectional/package integrity reconciliation is clean after controlled repair"

cat <<EOF

PASS - AMP storage reconciliation
---------------------------------
TARGETED clean             PASS
Payload missing detection  PASS
Payload drift detection    PASS
Annotation drift detection PASS
Manifest drift detection   PASS
TALLY count mismatch       PASS
FULL clean validation      PASS
Test prefix                $PREFIX
EOF
