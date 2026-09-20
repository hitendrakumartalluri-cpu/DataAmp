#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_S3_TEST_TENANT:-demo}"
BUCKET="${AMP_S3_TEST_BUCKET:-s3-test}"
TARGET_CONTAINER="${AMP_S3_TEST_CONTAINER:-amp-primary}"
EXPLICIT_GID="${AMP_S3_TEST_CATALOGUE_GROUP_ID:-}"
TOTAL="${AMP_S3_TEST_TOTAL:-10}"
CLEANUP="${AMP_S3_TEST_CLEANUP:-0}"
RUN_ID="${AMP_S3_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
PREFIX="${AMP_S3_TEST_PREFIX:-s3-interop-tests/${RUN_ID}/}"

fail(){ echo "[FAIL] $*" >&2; exit 1; }
ok(){ echo "[ok] $*"; }
info(){ echo "==> $*"; }

command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
curl -fsS "$BASE/healthz" >/dev/null || fail "AMP API is not healthy"

API_KEY="${AMP_API_KEY:-}"
if [[ -z "$API_KEY" && -f .env ]]; then
  API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' .env || true)"
fi
CURL_AUTH=()
[[ -n "$API_KEY" ]] && CURL_AUTH=(-H "x-api-key: $API_KEY")

if [[ -n "$EXPLICIT_GID" ]]; then
  GID="$EXPLICIT_GID"
  GROUP="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/api/v1/catalogue-groups/$GID")" || fail "cannot read explicit catalogue group $GID"
  [[ "$(jq -r 'type' <<<"$GROUP")" == "object" ]] || { echo "[debug] catalogue-group response: $GROUP" >&2; fail "explicit catalogue group API must return an object"; }
  [[ "$(jq -r '.tenant_id // empty' <<<"$GROUP")" == "$TENANT" ]] || fail "explicit catalogue group $GID does not belong to tenant $TENANT"
  [[ "$(jq -r '.state // empty' <<<"$GROUP")" == "ACTIVE" ]] || fail "explicit catalogue group $GID is not ACTIVE"
  TARGET_CONTAINER="$(jq -r '.container_name // empty' <<<"$GROUP")"
else
  GROUPS="$(curl -fsS "${CURL_AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT")" || fail "cannot list catalogue groups"
  [[ "$(jq -r 'type' <<<"$GROUPS")" == "array" ]] || { echo "[debug] catalogue-groups response: $GROUPS" >&2; fail "catalogue groups API must return an array"; }
  GID="$(jq -r --arg c "$TARGET_CONTAINER" '.[]|select(.container_name==$c and .state=="ACTIVE")|.id' <<<"$GROUPS" | head -1)"
  [[ -n "$GID" && "$GID" != "null" ]] || fail "No ACTIVE target catalogue group found"
fi

GROUP_DETAIL="${GROUP:-$(curl -fsS "${CURL_AUTH[@]}" "$BASE/api/v1/catalogue-groups/$GID")}"
PLACEMENT_MODE="$(jq -r '.package_placement_mode // "AMP_MANAGED_HASH"' <<<"$GROUP_DETAIL")"

info "Configuring shared protocol route '$TENANT/$BUCKET' -> $TARGET_CONTAINER"
curl -fsS "${CURL_AUTH[@]}" -X POST "$BASE/api/v1/gateway-routes" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$TENANT" --arg n "$BUCKET" --arg g "$GID" '{tenant_id:$t,namespace:$n,catalogue_group_id:$g,object_prefix:""}')" >/dev/null

cat <<EOF
AMP S3/HCP interoperability test
--------------------------------
Tenant          : $TENANT
S3 bucket       : $BUCKET
Target container: $TARGET_CONTAINER
Catalogue group : $GID
Placement mode  : $PLACEMENT_MODE
Object prefix   : $PREFIX
Objects         : $TOTAL via S3 + 1 via HCP
EOF

info "Phase 1/5 - ingest $TOTAL objects through the S3-compatible client API"
docker compose --env-file .env -f docker-compose.yml exec -T \
  -e TEST_BUCKET="$BUCKET" -e TEST_PREFIX="$PREFIX" -e TEST_TOTAL="$TOTAL" amp python - <<'PY'
import os, boto3, json
from botocore.config import Config
bucket=os.environ['TEST_BUCKET']; prefix=os.environ['TEST_PREFIX']; total=int(os.environ['TEST_TOTAL'])
s3=boto3.client('s3',endpoint_url='http://127.0.0.1:8080',
  aws_access_key_id=os.environ['AMP_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_S3_SECRET_KEY'],
  region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
for i in range(1,total+1):
    key=f"{prefix}s3-object-{i:03d}.json"
    body=json.dumps({'id':i,'createdThrough':'S3','title':f'AMP S3 object {i}'}).encode()
    s3.put_object(Bucket=bucket,Key=key,Body=body,ContentType='application/json',
                  Metadata={'jurisdiction':'UK','owner':'Legal'},
                  Tagging='recordType=CONTRACT&classification=CONFIDENTIAL')
print(total)
PY
ok "$TOTAL objects accepted by boto3/SigV4"

info "Phase 2/5 - validate S3 read/head/range/list and metadata/tag mappings"
docker compose --env-file .env -f docker-compose.yml exec -T \
  -e TEST_BUCKET="$BUCKET" -e TEST_PREFIX="$PREFIX" -e TEST_TOTAL="$TOTAL" amp python - <<'PY'
import os, boto3, json
from botocore.config import Config
bucket=os.environ['TEST_BUCKET']; prefix=os.environ['TEST_PREFIX']; total=int(os.environ['TEST_TOTAL'])
s3=boto3.client('s3',endpoint_url='http://127.0.0.1:8080',
  aws_access_key_id=os.environ['AMP_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_S3_SECRET_KEY'],
  region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
key=f"{prefix}s3-object-001.json"
r=s3.get_object(Bucket=bucket,Key=key)
body=r['Body'].read(); assert json.loads(body)['createdThrough']=='S3'
h=s3.head_object(Bucket=bucket,Key=key)
assert h['Metadata']['jurisdiction']=='UK' and h['Metadata']['owner']=='Legal'
rg=s3.get_object(Bucket=bucket,Key=key,Range='bytes=0-4')['Body'].read(); assert rg==body[:5]
t=s3.get_object_tagging(Bucket=bucket,Key=key)
tags={x['Key']:x['Value'] for x in t['TagSet']}; assert tags['recordType']=='CONTRACT'
lst=s3.list_objects_v2(Bucket=bucket,Prefix=prefix)
keys=[x['Key'] for x in lst.get('Contents',[])]; assert len(keys)==total and all(not k.startswith('.amp/') for k in keys)
print('ok')
PY
ok "S3 GET/HEAD/Range/ListObjectsV2/metadata/tags validated"

info "Phase 3/5 - S3-created object is readable through HCP REST"
FIRST_KEY="${PREFIX}s3-object-001.json"
HCP_BODY="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/rest/$TENANT/$BUCKET/$FIRST_KEY")" || fail "HCP GET of S3-created object failed"
[[ "$(jq -r '.createdThrough' <<<"$HCP_BODY")" == "S3" ]] || fail "cross-protocol S3 -> HCP payload mismatch"
ok "S3 PUT -> HCP GET works"

info "Phase 4/5 - HCP-created object is readable through S3"
HCP_KEY="${PREFIX}hcp-created.json"
curl -fsS "${CURL_AUTH[@]}" -X PUT "$BASE/rest/$TENANT/$BUCKET/$HCP_KEY" -H 'Content-Type: application/json' \
  --data-binary '{"createdThrough":"HCP","title":"cross protocol"}' >/dev/null

docker compose --env-file .env -f docker-compose.yml exec -T \
  -e TEST_BUCKET="$BUCKET" -e TEST_KEY="$HCP_KEY" amp python - <<'PY'
import os, boto3, json
from botocore.config import Config
s3=boto3.client('s3',endpoint_url='http://127.0.0.1:8080',
  aws_access_key_id=os.environ['AMP_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_S3_SECRET_KEY'],
  region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
r=s3.get_object(Bucket=os.environ['TEST_BUCKET'],Key=os.environ['TEST_KEY'])
assert json.loads(r['Body'].read())['createdThrough']=='HCP'
print('ok')
PY
ok "HCP PUT -> S3 GET works"

info "Phase 5/5 - validate AMP_PACKAGE_V3 physical isolation and logical catalogue"
ROWS="$(curl -fsS -G "$BASE/api/v1/catalogue-objects" \
  --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$GID" \
  --data-urlencode "q=$PREFIX" --data-urlencode "limit=$((TOTAL+20))")"
EXPECTED=$((TOTAL+1))
[[ "$(jq 'length' <<<"$ROWS")" -eq "$EXPECTED" ]] || fail "expected $EXPECTED logical catalogue rows"
[[ "$(jq '[.[]|select(.storage_layout=="AMP_PACKAGE_V3" and .source_mode=="GATEWAY")]|length' <<<"$ROWS")" -eq "$EXPECTED" ]] || fail "not all rows are GATEWAY/AMP_PACKAGE_V3"
[[ "$(jq '[.[]|select(.object_key|startswith(".amp/"))]|length' <<<"$ROWS")" -eq 0 ]] || fail "reserved package member leaked into logical catalogue"
FIRST_ID="$(jq -r --arg k "$FIRST_KEY" '.[]|select(.object_key==$k)|.id' <<<"$ROWS")"
DETAIL="$(curl -fsS "$BASE/api/v1/catalogue-objects/$FIRST_ID")"
ROOT_KEY="$(jq -r '.package_root' <<<"$DETAIL")"
if [[ "$PLACEMENT_MODE" == "AMP_MANAGED_HASH" ]]; then
  [[ "$ROOT_KEY" =~ ^\.amp/objects/[0-9a-fA-F]{2}/[0-9a-fA-F]{2}/[0-9a-fA-F-]{36}$ ]] || fail "managed package root mismatch: $ROOT_KEY"
else
  [[ "$ROOT_KEY" == "$FIRST_KEY/"* ]] || fail "client-path package root mismatch: $ROOT_KEY"
fi
[[ "$(jq -r '.payload_key' <<<"$DETAIL")" == "$ROOT_KEY/payload" ]] || fail "payload key mismatch"
[[ "$(jq -r '.manifest_key' <<<"$DETAIL")" == "$ROOT_KEY/manifest.json" ]] || fail "manifest key mismatch"
ANN="$(jq -r '[.annotations[].annotation_name]|sort|join(",")' <<<"$DETAIL")"
[[ "$ANN" == "s3-metadata,s3-tags" ]] || fail "S3 metadata/tag annotations missing: $ANN"
ok "reserved physical package namespace + logical catalogue separation validated"

if [[ "$CLEANUP" == "1" ]]; then
  info "Cleanup - delete test keys through S3"
  docker compose --env-file .env -f docker-compose.yml exec -T \
    -e TEST_BUCKET="$BUCKET" -e TEST_PREFIX="$PREFIX" amp python - <<'PY'
import os,boto3
from botocore.config import Config
s3=boto3.client('s3',endpoint_url='http://127.0.0.1:8080',aws_access_key_id=os.environ['AMP_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_S3_SECRET_KEY'],region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
r=s3.list_objects_v2(Bucket=os.environ['TEST_BUCKET'],Prefix=os.environ['TEST_PREFIX'])
for x in r.get('Contents',[]): s3.delete_object(Bucket=os.environ['TEST_BUCKET'],Key=x['Key'])
PY
  ok "logical test objects deleted; catalogue tombstones retained"
fi

cat <<EOF

PASS - AMP S3/HCP protocol interoperability
--------------------------------------------
S3-created objects          $TOTAL
HCP-created objects         1
Package layout              AMP_PACKAGE_V3
Placement mode              $PLACEMENT_MODE
S3 -> HCP read              PASS
HCP -> S3 read              PASS
S3 metadata sidecar         PASS
S3 tags sidecar             PASS
Range GET                   PASS
ListObjectsV2               PASS
Reserved member leakage     0
Test prefix                 $PREFIX
EOF
