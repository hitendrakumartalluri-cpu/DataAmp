#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
BASE="${AMP_URL:-http://127.0.0.1:8080}"
fail(){ echo "[FAIL] $*" >&2; exit 1; }; ok(){ echo "[ok] $*"; }
command -v curl >/dev/null || fail "curl required"; command -v jq >/dev/null || fail "jq required"
H="$(curl -fsS "$BASE/healthz")" || fail "AMP health endpoint unavailable"
[[ "$(jq -r .version <<<"$H")" == "0.9.0-beta.5.0" ]] || fail "expected beta.5.0, got $(jq -r .version <<<"$H")"
ok "AMP 0.9.0-beta.5.0 is running"
G="$(curl -fsS "$BASE/api/v1/catalogue-groups?tenant_id=demo")"; [[ "$(jq -r type <<<"$G")" == array ]] || fail "catalogue-groups response is not array"
P="$(jq -r '.[]|select(.container_name=="amp-primary")|.id' <<<"$G" | head -1)"; L="$(jq -r '.[]|select(.container_name=="legacy-hcp")|.id' <<<"$G" | head -1)"
[[ -n "$P" && -n "$L" ]] || fail "primary/legacy catalogue groups missing"
ok "primary catalogue $P"
ok "legacy catalogue $L"
C="$(curl -fsS "$BASE/api/v1/change-capture?tenant_id=demo")"
[[ "$(jq -r --arg g "$P" '.[]|select(.catalogue_group_id==$g)|.config.raw_topic' <<<"$C")" == "amp.raw.minio.primary" ]] || fail "primary change-capture raw topic incorrect"
[[ "$(jq -r --arg g "$L" '.[]|select(.catalogue_group_id==$g)|.config.raw_topic' <<<"$C")" == "amp.raw.minio.legacy" ]] || fail "legacy change-capture raw topic incorrect"
ok "change-capture routing configured"
docker compose --env-file .env -f docker-compose.yml exec -T amp python - <<'PY'
import os,boto3
from botocore.config import Config
for label,prefix in [('primary','PRIMARY'),('legacy','LEGACY')]:
    c=boto3.client('s3',endpoint_url=os.environ[f'AMP_LAB_{prefix}_ENDPOINT'],aws_access_key_id=os.environ[f'AMP_LAB_{prefix}_ACCESS_KEY'],aws_secret_access_key=os.environ[f'AMP_LAB_{prefix}_SECRET_KEY'],region_name='us-east-1',config=Config(s3={'addressing_style':'path'}))
    b=os.environ[f'AMP_LAB_{prefix}_BUCKET']
    st=c.get_bucket_versioning(Bucket=b).get('Status','')
    if st!='Enabled': raise SystemExit(f'{label} bucket versioning is {st!r}, expected Enabled')
    print(f'[ok] {label} backend-native versioning Enabled on {b}')
PY
ok "beta.5 readiness checks passed"
echo "PRIMARY_CATALOGUE=$P"
echo "LEGACY_CATALOGUE=$L"
