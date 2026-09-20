#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
BASE="${AMP_URL:-http://127.0.0.1:8080}"; TENANT="${AMP_RESPONSE_TEST_TENANT:-demo}"; BUCKET="${AMP_RESPONSE_TEST_BUCKET:-response-test}"; GID="${AMP_RESPONSE_TEST_CATALOGUE_GROUP_ID:-}"
fail(){ echo "[FAIL] $*" >&2; exit 1; }; ok(){ echo "[ok] $*"; }
command -v curl >/dev/null || fail "curl required"; command -v jq >/dev/null || fail "jq required"; curl -fsS "$BASE/healthz" >/dev/null || fail "AMP not healthy"
API_KEY="${AMP_API_KEY:-}"; [[ -z "$API_KEY" && -f .env ]] && API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' .env || true)"; AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")
if [[ -z "$GID" ]]; then GID="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT" | jq -r '.[]|select(.container_name=="amp-primary" and .state=="ACTIVE")|.id' | head -1)"; fi
[[ -n "$GID" && "$GID" != null ]] || fail "target catalogue group not found"
config(){ curl -fsS "${AUTH[@]}" -X POST "$BASE/api/v1/gateway-routes" -H 'Content-Type: application/json' -d "$(jq -nc --arg t "$TENANT" --arg n "$BUCKET" --arg g "$GID" --arg m "$1" --arg h "$2" '{tenant_id:$t,namespace:$n,catalogue_group_id:$g,response_mode:$m,backend_header_policy:$h,capture_backend_response:true,add_amp_request_id:true}')" >/dev/null; }

echo "AMP Gateway response policy test"
echo "--------------------------------"
config AMP_NORMALIZED SELECTED
R=$(curl -sS "${AUTH[@]}" -w '\n%{http_code}' "$BASE/rest/$TENANT/$BUCKET/missing-does-not-exist.txt"); BODY="${R%$'\n'*}"; CODE="${R##*$'\n'}"
[[ "$CODE" == 404 ]] || fail "normalized mode expected 404, got $CODE"; [[ "$(jq -r .error <<<"$BODY")" == HTTP_STATUS_404 ]] || fail "normalized mode did not return HTTP_STATUS_404"
ok "AMP_NORMALIZED returns stable generic HTTP_STATUS_404"
config AMP_NORMALIZED_WITH_BACKEND SELECTED
R=$(curl -sS "${AUTH[@]}" -w '\n%{http_code}' "$BASE/rest/$TENANT/$BUCKET/missing-does-not-exist.txt"); BODY="${R%$'\n'*}"; CODE="${R##*$'\n'}"
[[ "$CODE" == 404 ]] || fail "normalized+backend expected 404"; [[ "$(jq -r '.backend.status' <<<"$BODY")" == 404 ]] || fail "backend diagnostics not included"
ok "AMP_NORMALIZED_WITH_BACKEND adds backend diagnostics without changing outcome"
config RAW_BACKEND ALL_SAFE
# Use the S3 frontend so the raw S3-compatible backend error remains an S3 XML error to boto3.
docker compose --env-file .env -f docker-compose.yml exec -T -e TEST_BUCKET="$BUCKET" amp python - <<'PY'
import os,boto3
from botocore.config import Config
from botocore.exceptions import ClientError
s3=boto3.client('s3',endpoint_url='http://127.0.0.1:8080',aws_access_key_id=os.environ['AMP_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_S3_SECRET_KEY'],region_name='us-east-1',config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
try:
    s3.get_object(Bucket=os.environ['TEST_BUCKET'],Key='missing-does-not-exist.txt')
    raise SystemExit('expected ClientError')
except ClientError as e:
    code=e.response.get('Error',{}).get('Code','')
    if code in {'HTTP_STATUS_404',''}: raise SystemExit(f'expected backend-native code, got {code}')
    print('raw backend code:',code)
PY
ok "RAW_BACKEND preserves backend-native error code/status for S3-compatible clients"
TX=$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/backend-transactions" --data-urlencode "tenant_id=$TENANT" --data-urlencode "limit=20"); [[ "$(jq '[.[]|select(.outcome=="FAILED")]|length' <<<"$TX")" -ge 2 ]] || fail "backend failure transactions were not captured"
ok "raw backend metadata captured in audit transaction history"
echo; echo "PASS - configurable Gateway response policy"
