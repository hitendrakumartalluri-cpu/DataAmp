#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
BASE="${AMP_URL:-http://127.0.0.1:8080}"; TENANT="${AMP_MIG_TEST_TENANT:-demo}"
SRC_CONTAINER="${AMP_MIG_TEST_SOURCE_CONTAINER:-legacy-hcp}"; TGT_CONTAINER="${AMP_MIG_TEST_TARGET_CONTAINER:-amp-primary}"
RUN="${AMP_MIG_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"; PREFIX="migration-tests/$RUN/"; TIMEOUT="${AMP_MIG_TEST_TIMEOUT:-300}"
fail(){ echo "[FAIL] $*" >&2; exit 1; }; ok(){ echo "[ok] $*"; }
command -v curl >/dev/null || fail "curl required"; command -v jq >/dev/null || fail "jq required"
curl -fsS "$BASE/healthz" >/dev/null || fail "AMP not healthy"
API_KEY="${AMP_API_KEY:-}"; [[ -z "$API_KEY" && -f .env ]] && API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' .env || true)"; AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")
GROUPS="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT")"
[[ "$(jq -r type <<<"$GROUPS")" == array ]] || fail "catalogue groups API did not return array"
SRC="$(jq -r --arg c "$SRC_CONTAINER" '.[]|select(.container_name==$c and .state=="ACTIVE")|.id' <<<"$GROUPS" | head -1)"
TGT="$(jq -r --arg c "$TGT_CONTAINER" '.[]|select(.container_name==$c and .state=="ACTIVE")|.id' <<<"$GROUPS" | head -1)"
[[ -n "$SRC" && "$SRC" != null && -n "$TGT" && "$TGT" != null ]] || fail "source/target catalogue not found"

echo "AMP migration + hydration integration test"
echo "-----------------------------------------"
echo "Source catalogue : $SRC ($SRC_CONTAINER)"; echo "Target catalogue : $TGT ($TGT_CONTAINER)"; echo "Prefix           : $PREFIX"

echo "==> Creating 4 objects directly in legacy storage"
docker compose --env-file .env -f docker-compose.yml exec -T -e P="$PREFIX" amp python - <<'PY'
import os,json,boto3
from botocore.config import Config
c=boto3.client('s3',endpoint_url=os.environ['AMP_LAB_LEGACY_ENDPOINT'],aws_access_key_id=os.environ['AMP_LAB_LEGACY_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_LAB_LEGACY_SECRET_KEY'],region_name='us-east-1',config=Config(s3={'addressing_style':'path'}))
b=os.environ['AMP_LAB_LEGACY_BUCKET']; p=os.environ['P']
for i in range(1,5):
    c.put_object(Bucket=b,Key=f'{p}obj-{i}.json',Body=json.dumps({'migrationTest':True,'object':i}).encode(),ContentType='application/json')
print('created 4')
PY

deadline=$((SECONDS+TIMEOUT))
while (( SECONDS < deadline )); do
  N="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$SRC" --data-urlencode "q=$PREFIX" --data-urlencode "limit=20" | jq '[.[]|select(.lifecycle_state=="ACTIVE")]|length')"
  (( N >= 4 )) && break
  sleep 2
done
(( N >= 4 )) || fail "source catalogue did not receive four external objects"
ok "external source objects catalogued through change capture"

echo "==> Dry-run migration for first three objects"
DRY="$(curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/migrations" -H 'Content-Type: application/json' -d "$(jq -nc --arg t "$TENANT" --arg s "$SRC" --arg g "$TGT" --arg p "${PREFIX}obj-" '{tenant_id:$t,source_group_id:$s,target_group_id:$g,prefix:$p,dry_run:true}')")"
[[ "$(jq -r .dry_run <<<"$DRY")" == true ]] || fail "dry run not reported"
[[ "$(jq -r .copied <<<"$DRY")" -ge 4 ]] || fail "dry run did not select expected objects"
ok "dry-run reports copy plan without target writes"

echo "==> Migrating current logical objects"
MIG="$(curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/migrations" -H 'Content-Type: application/json' -d "$(jq -nc --arg t "$TENANT" --arg s "$SRC" --arg g "$TGT" --arg p "$PREFIX" '{tenant_id:$t,source_group_id:$s,target_group_id:$g,prefix:$p,dry_run:false}')")"
[[ "$(jq -r .errors <<<"$MIG")" == 0 ]] || { echo "$MIG" | jq .; fail "migration reported errors"; }
[[ "$(jq -r .copied <<<"$MIG")" -ge 4 ]] || fail "migration did not copy expected objects"
TARGET_ROWS="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$TGT" --data-urlencode "q=$PREFIX" --data-urlencode "limit=20")"
[[ "$(jq 'length' <<<"$TARGET_ROWS")" -ge 4 ]] || fail "target catalogue missing migrated rows"
[[ "$(jq '[.[]|select(.storage_layout=="AMP_PACKAGE_V3" and .source_mode=="MIGRATED" and (.origin_recon_id//"")!="")]|length' <<<"$TARGET_ROWS")" -ge 4 ]] || fail "target migration rows do not have package/lineage state"
ok "migration created independent target package records with source lineage"

# Create one more object after migration and exercise read-through hydration only for it.
HKEY="${PREFIX}hydrate-only.json"
echo "==> Creating one post-migration object and hydrating it on read"
docker compose --env-file .env -f docker-compose.yml exec -T -e K="$HKEY" amp python - <<'PY'
import os,boto3
from botocore.config import Config
c=boto3.client('s3',endpoint_url=os.environ['AMP_LAB_LEGACY_ENDPOINT'],aws_access_key_id=os.environ['AMP_LAB_LEGACY_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_LAB_LEGACY_SECRET_KEY'],region_name='us-east-1',config=Config(s3={'addressing_style':'path'}))
c.put_object(Bucket=os.environ['AMP_LAB_LEGACY_BUCKET'],Key=os.environ['K'],Body=b'hydrate-me',ContentType='text/plain')
PY
SRC_OBJ=""
deadline=$((SECONDS+TIMEOUT))
while (( SECONDS < deadline )); do
  SRC_OBJ="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$SRC" --data-urlencode "q=$HKEY" --data-urlencode "limit=10" | jq -r '.[0].id // empty')"
  [[ -n "$SRC_OBJ" ]] && break
  sleep 2
done
[[ -n "$SRC_OBJ" ]] || fail "hydrate source object not catalogued"
# Target must not be registered before successful hydration write.
PRE="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$TGT" --data-urlencode "q=$HKEY" --data-urlencode "limit=10" | jq 'length')"
[[ "$PRE" == 0 ]] || fail "hydrate target existed before hydration"
BODY="$(curl -fsS "${AUTH[@]}" "$BASE/api/v1/catalogue-objects/$SRC_OBJ/content?hydrate_target_group_id=$TGT")"
[[ "$BODY" == hydrate-me ]] || fail "hydration read returned wrong payload"
POST="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$TGT" --data-urlencode "q=$HKEY" --data-urlencode "limit=10")"
[[ "$(jq 'length' <<<"$POST")" == 1 ]] || fail "hydrate target not registered after target write"
[[ "$(jq -r '.[0].source_mode' <<<"$POST")" == HYDRATED ]] || fail "target source_mode is not HYDRATED"
[[ "$(jq -r '.[0].storage_layout' <<<"$POST")" == AMP_PACKAGE_V3 ]] || fail "hydration target is not AMP_PACKAGE_V3"
ok "read-through hydration registers target only after physical target write"

echo; echo "PASS - migration + hydration"
echo "Migration copied: $(jq -r .copied <<<"$MIG")"
echo "Hydration: PASS"
