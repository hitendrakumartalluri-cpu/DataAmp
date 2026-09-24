#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_RECON_TEST_TENANT:-demo}"
TARGET_CONTAINER="${AMP_RECON_TEST_CONTAINER:-amp-primary}"
RUN_ID="${AMP_RECON_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
PREFIX="${AMP_RECON_TEST_PREFIX:-reconciliation-tests/$RUN_ID/}"
GID="${AMP_RECON_TEST_CATALOGUE_GROUP_ID:-}"

fail(){ echo "[FAIL] $*" >&2; exit 1; }
ok(){ echo "[ok] $*"; }
API_KEY="${AMP_API_KEY:-}"
AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")

curl -fsS "$BASE/healthz" >/dev/null || fail "AMP API is not healthy"
if [[ -z "$GID" ]]; then
  groups="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT")"
  GID="$(jq -r --arg c "$TARGET_CONTAINER" '.[]|select(.container_name==$c and .state=="ACTIVE")|.id' <<<"$groups" | head -1)"
fi
[[ -n "$GID" && "$GID" != "null" ]] || fail "catalogue group not found"

storage_write(){
  docker compose --env-file .env -f docker-compose.yml exec -T -e TEST_PREFIX="$PREFIX" -e TEST_ACTION="$1" amp python - <<'PY'
import os,boto3
from botocore.config import Config
s3=boto3.client('s3',endpoint_url=os.environ['AMP_LAB_PRIMARY_ENDPOINT'],aws_access_key_id=os.environ['AMP_LAB_PRIMARY_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_LAB_PRIMARY_SECRET_KEY'],region_name='us-east-1',config=Config(s3={'addressing_style':'path'}))
bucket=os.environ['AMP_LAB_PRIMARY_BUCKET']; prefix=os.environ['TEST_PREFIX']; action=os.environ['TEST_ACTION']
if action=='create':
    for i in range(1,5): s3.put_object(Bucket=bucket,Key=f'{prefix}object-{i}.json',Body=(f'{{"id":{i}}}').encode(),ContentType='application/json')
elif action=='delete': s3.delete_object(Bucket=bucket,Key=f'{prefix}object-1.json')
elif action=='restore': s3.put_object(Bucket=bucket,Key=f'{prefix}object-1.json',Body=b'{"id":1}',ContentType='application/json')
PY
}

discover(){ curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/discovery/run" -H 'Content-Type: application/json' -d "$(jq -nc --arg t "$TENANT" --arg g "$GID" --arg p "$PREFIX" '{tenant_id:$t,catalogue_group_id:$g,prefix:$p,auto_index:true}')"; }
reconcile(){ curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/reconciliation/run" -H 'Content-Type: application/json' -d "$(jq -nc --arg t "$TENANT" --arg g "$GID" --arg p "$PREFIX" '{tenant_id:$t,catalogue_group_id:$g,target:"STORAGE",prefix:$p,storage_mode:"FULL",verify_package_members:false,limit:100}')"; }

storage_write create
D="$(discover)"; [[ "$(jq '.registered' <<<"$D")" -eq 4 ]] || fail "discovery did not register four objects"
R="$(reconcile)"; [[ "$(jq '.findings' <<<"$R")" -eq 0 ]] || fail "clean reconciliation produced findings"
ok "clean direct-object reconciliation"

storage_write delete
R="$(reconcile)"; [[ "$(jq -r '.finding_counts.MISSING_FROM_STORAGE // 0' <<<"$R")" -ge 1 ]] || fail "missing object not detected"
ok "missing object detected"

storage_write restore
discover >/dev/null
R="$(reconcile)"; [[ "$(jq '.findings' <<<"$R")" -eq 0 ]] || fail "repaired reconciliation produced findings"
ok "repaired source returned to clean state"
echo "PASS - connector-based storage reconciliation"
