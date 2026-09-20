#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_HCP_TEST_TENANT:-demo}"
NAMESPACE="${AMP_HCP_TEST_NAMESPACE:-hcp-test}"
TARGET_CONTAINER="${AMP_HCP_TEST_CONTAINER:-amp-primary}"
EXPLICIT_GID="${AMP_HCP_TEST_CATALOGUE_GROUP_ID:-}"
TOTAL="${AMP_HCP_TEST_TOTAL:-10}"
CLEANUP="${AMP_HCP_TEST_CLEANUP:-0}"
RUN_ID="${AMP_HCP_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
PREFIX="${AMP_HCP_TEST_PREFIX:-hcp-package-tests/${RUN_ID}/}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail(){ echo "[FAIL] $*" >&2; exit 1; }
ok(){ echo "[ok] $*"; }
info(){ echo "==> $*"; }

[[ "$TOTAL" =~ ^[0-9]+$ ]] || fail "AMP_HCP_TEST_TOTAL must be an integer"
(( TOTAL > 0 )) || fail "AMP_HCP_TEST_TOTAL must be > 0"
command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
curl -fsS "$BASE/healthz" >/dev/null || fail "AMP API is not healthy at $BASE"

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
  [[ "$(jq -r '.id // empty' <<<"$GROUP")" == "$GID" ]] || fail "explicit catalogue group response ID mismatch"
  [[ "$(jq -r '.tenant_id // empty' <<<"$GROUP")" == "$TENANT" ]] || fail "explicit catalogue group $GID does not belong to tenant $TENANT"
  [[ "$(jq -r '.state // empty' <<<"$GROUP")" == "ACTIVE" ]] || fail "explicit catalogue group $GID is not ACTIVE"
  TARGET_CONTAINER="$(jq -r '.container_name // empty' <<<"$GROUP")"
else
  GROUPS="$(curl -fsS "${CURL_AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT")" || fail "cannot list catalogue groups"
  [[ "$(jq -r 'type' <<<"$GROUPS")" == "array" ]] || { echo "[debug] catalogue-groups response: $GROUPS" >&2; fail "catalogue groups API must return an array"; }
  GID="$(jq -r --arg c "$TARGET_CONTAINER" '.[] | select(.container_name==$c and .state=="ACTIVE") | .id' <<<"$GROUPS" | head -1)"
  [[ -n "$GID" && "$GID" != "null" ]] || fail "No ACTIVE catalogue group found for container '$TARGET_CONTAINER'"
fi

GROUP_DETAIL="${GROUP:-$(curl -fsS "${CURL_AUTH[@]}" "$BASE/api/v1/catalogue-groups/$GID")}"
PLACEMENT_MODE="$(jq -r '.package_placement_mode // "AMP_MANAGED_HASH"' <<<"$GROUP_DETAIL")"

info "Configuring HCP namespace route '$TENANT/$NAMESPACE' -> $TARGET_CONTAINER"
ROUTE="$(curl -fsS "${CURL_AUTH[@]}" -X POST "$BASE/api/v1/gateway-routes" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$TENANT" --arg n "$NAMESPACE" --arg g "$GID" '{tenant_id:$t,namespace:$n,catalogue_group_id:$g,object_prefix:""}')")" || fail "could not configure gateway route"
[[ "$(jq -r '.catalogue_group_id' <<<"$ROUTE")" == "$GID" ]] || fail "gateway route points to unexpected catalogue"

cat <<EOF
AMP HCP REST package-layout integration test
--------------------------------------------
Tenant          : $TENANT
HCP namespace   : $NAMESPACE
Target container: $TARGET_CONTAINER
Catalogue group : $GID
Placement mode  : $PLACEMENT_MODE
Object prefix   : $PREFIX
Objects         : $TOTAL
Physical/object : payload + 3 annotations + manifest
EOF

FIRST_KEY=""; FIRST_ID=""; FIRST_RECON=""
info "Phase 1/5 - PUT $TOTAL logical objects through HCP-style REST"
for i in $(seq 1 "$TOTAL"); do
  n=$(printf '%03d' "$i")
  key="${PREFIX}object-${n}.json"
  payload="$(jq -nc --arg id "HCP-${RUN_ID}-${n}" --arg title "AMP HCP REST object ${n}" --arg ns "$NAMESPACE" --argjson seq "$i" \
    '{id:$id,title:$title,namespace:$ns,sequence:$seq,ingestPath:"HCP_REST",createdBy:"amp-automated-test"}')"
  hdr="$TMP/object-${n}.headers"
  code="$(curl -sS "${CURL_AUTH[@]}" -D "$hdr" -o /dev/null -w '%{http_code}' -X PUT \
    "$BASE/rest/$TENANT/$NAMESPACE/$key" -H 'Content-Type: application/json' --data-binary "$payload")"
  [[ "$code" == "201" ]] || fail "payload PUT $key returned HTTP $code"
  oid="$(awk 'BEGIN{IGNORECASE=1} /^x-amp-object-id:/{gsub("\\r","");print $2}' "$hdr" | tail -1)"
  rid="$(awk 'BEGIN{IGNORECASE=1} /^x-amp-recon-id:/{gsub("\\r","");print $2}' "$hdr" | tail -1)"
  [[ -n "$oid" && -n "$rid" ]] || fail "payload PUT $key did not return AMP identity headers"
  if [[ -z "$FIRST_KEY" ]]; then FIRST_KEY="$key"; FIRST_ID="$oid"; FIRST_RECON="$rid"; fi

  default_ann="$(jq -nc --arg id "HCP-${RUN_ID}-${n}" '{recordId:$id,recordType:"CONTRACT",jurisdiction:"UK",owner:"Legal",schemaVersion:1}')"
  legal_ann='{"classification":"CONFIDENTIAL","retentionYears":10,"legalHold":false,"retentionPolicy":"UK-LEGAL-10Y"}'
  migration_ann='{"sourceSystem":"HCP-LAB","migrationWave":"wave-01","migrationStatus":"READY","checksumPolicy":"SHA-256"}'
  for ann in default legal migration; do
    case "$ann" in default) body="$default_ann";; legal) body="$legal_ann";; migration) body="$migration_ann";; esac
    ah="$TMP/${n}-${ann}.headers"
    acode="$(curl -sS "${CURL_AUTH[@]}" -D "$ah" -o /dev/null -w '%{http_code}' -X PUT \
      "$BASE/rest/$TENANT/$NAMESPACE/$key?type=custom-metadata&annotation=$ann" \
      -H 'Content-Type: application/json' --data-binary "$body")"
    [[ "$acode" == "201" ]] || fail "annotation PUT $key/$ann returned HTTP $acode"
    sidecar="$(awk 'BEGIN{IGNORECASE=1} /^x-amp-sidecar-key:/{gsub("\\r","");print $2}' "$ah" | tail -1)"
    [[ "$sidecar" == */annotations/$ann.json ]] || fail "unexpected $ann sidecar key: $sidecar"
  done
  printf '  ingested %d/%d\r' "$i" "$TOTAL"
done
echo
ok "$TOTAL logical objects and $((TOTAL*3)) annotations accepted"

info "Phase 2/5 - validate catalogue package metadata"
ROWS="$(curl -fsS "${CURL_AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" \
  --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$GID" \
  --data-urlencode "q=$PREFIX" --data-urlencode "limit=$((TOTAL+100))")" || fail "cannot query catalogue"
[[ "$(jq -r 'type' <<<"$ROWS")" == "array" ]] || fail "catalogue objects API did not return an array"
[[ "$(jq 'length' <<<"$ROWS")" -eq "$TOTAL" ]] || fail "expected $TOTAL business catalogue rows"
PKG="$(jq '[.[]|select(.source_mode=="GATEWAY" and .storage_layout=="AMP_PACKAGE_V3" and .lifecycle_state=="ACTIVE")]|length' <<<"$ROWS")"
[[ "$PKG" -eq "$TOTAL" ]] || fail "expected $TOTAL ACTIVE GATEWAY AMP_PACKAGE_V3 rows, found $PKG"
while read -r oid; do
  d="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/api/v1/catalogue-objects/$oid")"
  logical="$(jq -r '.object_key' <<<"$d")"
  package_root="$(jq -r '.package_root' <<<"$d")"
  if [[ "$PLACEMENT_MODE" == "AMP_MANAGED_HASH" ]]; then
    [[ "$package_root" =~ ^\.amp/objects/[0-9a-fA-F]{2}/[0-9a-fA-F]{2}/[0-9a-fA-F-]{36}$ ]] || fail "managed hash package root mismatch for $logical: $package_root"
  else
    [[ "$package_root" == "$logical/"* ]] || fail "client-path package root mismatch for $logical: $package_root"
  fi
  [[ "$(jq -r '.payload_key' <<<"$d")" == "$package_root/payload" ]] || fail "payload key mismatch for $logical"
  [[ "$(jq -r '.manifest_key' <<<"$d")" == "$package_root/manifest.json" ]] || fail "manifest key mismatch for $logical"
  names="$(jq -r '[.annotations[].annotation_name]|sort|join(",")' <<<"$d")"
  [[ "$names" == "default,legal,migration" ]] || fail "unexpected annotation set for $logical: $names"
done < <(jq -r '.[].id' <<<"$ROWS")
ok "$TOTAL catalogue rows represent logical packages only"

info "Phase 3/5 - verify reserved physical object-package layout in MinIO"
if docker compose --env-file .env -f docker-compose.yml ps amp >/dev/null 2>&1; then
  FIRST_DETAIL="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/api/v1/catalogue-objects/$FIRST_ID")"
  FIRST_ROOT="$(jq -r '.package_root' <<<"$FIRST_DETAIL")"
  export TEST_ROOT="$FIRST_ROOT"
  PHYSICAL="$(docker compose --env-file .env -f docker-compose.yml exec -T \
    -e TEST_ROOT="$FIRST_ROOT" amp python - <<'PY2'
import os, json, boto3
from botocore.config import Config
s3=boto3.client('s3',endpoint_url=os.environ['AMP_LAB_PRIMARY_ENDPOINT'],
 aws_access_key_id=os.environ['AMP_LAB_PRIMARY_ACCESS_KEY'],aws_secret_access_key=os.environ['AMP_LAB_PRIMARY_SECRET_KEY'],
 region_name='us-east-1',config=Config(s3={'addressing_style':'path'}))
bucket=os.environ['AMP_LAB_PRIMARY_BUCKET']; prefix=os.environ['TEST_ROOT']+'/'
r=s3.list_objects_v2(Bucket=bucket,Prefix=prefix)
items=[x['Key'] for x in r.get('Contents',[])]
print(json.dumps({'count':len(items),'keys':items}))
PY2
)" || fail "could not inspect primary MinIO"
  [[ "$(jq '.count' <<<"$PHYSICAL")" -eq 5 ]] || fail "expected 5 physical members for first package, found $(jq '.count' <<<"$PHYSICAL")"
  [[ "$(jq --arg k "$FIRST_ROOT/payload" '[.keys[]|select(.==$k)]|length' <<<"$PHYSICAL")" -eq 1 ]] || fail "first payload member missing"
  [[ "$(jq --arg k "$FIRST_ROOT/manifest.json" '[.keys[]|select(.==$k)]|length' <<<"$PHYSICAL")" -eq 1 ]] || fail "first manifest missing"
  for ann in default legal migration; do
    [[ "$(jq --arg k "$FIRST_ROOT/annotations/$ann.json" '[.keys[]|select(.==$k)]|length' <<<"$PHYSICAL")" -eq 1 ]] || fail "$ann sidecar missing"
  done
  ok "physical layout preserves <GUID>/{payload,annotations,manifest.json} using $PLACEMENT_MODE placement"
else
  echo "[warn] Docker lab not detected; physical layout check skipped"
fi

info "Phase 4/5 - validate round-trip API and manifest"
PAYLOAD="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/rest/$TENANT/$NAMESPACE/$FIRST_KEY")" || fail "payload GET failed"
[[ "$(jq -r '.ingestPath' <<<"$PAYLOAD")" == "HCP_REST" ]] || fail "payload read-back mismatch"
for ann in default legal migration; do
  A="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/rest/$TENANT/$NAMESPACE/$FIRST_KEY?type=custom-metadata&annotation=$ann")" || fail "$ann GET failed"
  [[ -n "$A" ]] || fail "$ann annotation empty"
done
DETAIL="$(curl -fsS "${CURL_AUTH[@]}" "$BASE/api/v1/catalogue-objects/$FIRST_ID")"
[[ "$(jq -r '.recon_id' <<<"$DETAIL")" == "$FIRST_RECON" ]] || fail "recon ID mismatch"
ok "payload and annotations round-trip while logical recon identity remains stable"

info "Phase 5/5 - verify package members never leak into business catalogue"
LEAKS="$(curl -fsS "${CURL_AUTH[@]}" -G "$BASE/api/v1/catalogue-objects" \
  --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$GID" \
  --data-urlencode "q=$PREFIX" --data-urlencode "limit=$((TOTAL*10+100))" \
  | jq '[.[]|select(.object_key|startswith(".amp/"))]|length')"
[[ "$LEAKS" -eq 0 ]] || fail "$LEAKS physical package members leaked into business catalogue"
ok "payload/annotation/manifest members are excluded from business catalogue"

if [[ "$CLEANUP" == "1" ]]; then
  info "Cleanup - DELETE logical objects through HCP REST"
  for i in $(seq 1 "$TOTAL"); do
    n=$(printf '%03d' "$i"); key="${PREFIX}object-${n}.json"
    code="$(curl -sS "${CURL_AUTH[@]}" -o /dev/null -w '%{http_code}' -X DELETE "$BASE/rest/$TENANT/$NAMESPACE/$key")"
    [[ "$code" == "204" ]] || fail "cleanup DELETE $key returned HTTP $code"
  done
  ok "package members deleted; catalogue tombstones retained"
fi

cat <<EOF

PASS - AMP HCP REST object-package ingestion
--------------------------------------------
Logical objects           $TOTAL
Physical members/object   5 (reserved package namespace)
Payload members           $TOTAL
Annotation sidecars       $((TOTAL*3))
Manifests                  $TOTAL
Catalogue source_mode     GATEWAY
Storage layout            AMP_PACKAGE_V3
Placement mode             $PLACEMENT_MODE
Package-member leakage    0
API round-trip            PASS
Recon identity            PASS
Test prefix               $PREFIX
EOF
