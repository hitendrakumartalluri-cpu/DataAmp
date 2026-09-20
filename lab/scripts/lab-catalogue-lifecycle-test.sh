#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-.env}"
BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_TEST_TENANT:-demo}"
CONTAINER="${AMP_TEST_CONTAINER:-legacy-hcp}"
TOTAL="${AMP_TEST_TOTAL:-500}"
UPDATE_COUNT="${AMP_TEST_UPDATES:-50}"
DELETE_COUNT="${AMP_TEST_DELETES:-25}"
TIMEOUT="${AMP_TEST_TIMEOUT:-900}"
POLL_SECONDS="${AMP_TEST_POLL_SECONDS:-2}"
CLEANUP="${AMP_TEST_CLEANUP:-0}"
RUN_ID="${AMP_TEST_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
PREFIX="${AMP_TEST_PREFIX:-amp-tests/catalogue-lifecycle/${RUN_ID}/}"
LIMIT=$((TOTAL + 100))
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
ok() { echo "[ok] $*"; }
info() { echo "==> $*"; }

[[ "$TOTAL" =~ ^[0-9]+$ ]] || fail "AMP_TEST_TOTAL must be an integer"
[[ "$UPDATE_COUNT" =~ ^[0-9]+$ ]] || fail "AMP_TEST_UPDATES must be an integer"
[[ "$DELETE_COUNT" =~ ^[0-9]+$ ]] || fail "AMP_TEST_DELETES must be an integer"
(( UPDATE_COUNT + DELETE_COUNT <= TOTAL )) || fail "updates + deletes must be <= total"

command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
command -v python3 >/dev/null || fail "python3 is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"

curl -fsS "$BASE/healthz" >/dev/null || fail "AMP API is not healthy at $BASE"

# Resolve the catalogue group with strict JSON shape validation.
# This avoids opaque jq failures when an endpoint unexpectedly returns a scalar/error payload.
GROUPS_FILE="$TMP/groups.json"
if ! curl -fsSG "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT" -o "$GROUPS_FILE"; then
  fail "Unable to read catalogue groups from $BASE"
fi
if ! jq -e 'type == "array"' "$GROUPS_FILE" >/dev/null 2>&1; then
  echo "[debug] Unexpected /api/v1/catalogue-groups response:" >&2
  cat "$GROUPS_FILE" >&2; echo >&2
  fail "Catalogue groups API did not return a JSON array"
fi
GID="$(python3 - "$GROUPS_FILE" "$CONTAINER" <<'PY2'
import json,sys
rows=json.load(open(sys.argv[1]))
container=sys.argv[2]
for g in rows:
    if isinstance(g,dict) and g.get('container_name')==container and g.get('state')=='ACTIVE':
        print(g['id']); break
PY2
)"
[[ -n "$GID" ]] || fail "No ACTIVE catalogue group found for container '$CONTAINER'"

CAPTURE_FILE="$TMP/capture.json"
if ! curl -fsS "$BASE/api/v1/catalogue-groups/$GID/change-capture" -o "$CAPTURE_FILE"; then
  fail "Unable to read change-capture configuration for catalogue $GID"
fi
if ! jq -e 'type == "object"' "$CAPTURE_FILE" >/dev/null 2>&1; then
  echo "[debug] Unexpected change-capture response:" >&2
  cat "$CAPTURE_FILE" >&2; echo >&2
  fail "Change-capture API did not return a JSON object"
fi
MODE="$(jq -r '.mode // "NONE"' "$CAPTURE_FILE")"
ENABLED="$(jq -r '.enabled // false' "$CAPTURE_FILE")"
RAW_TOPIC="$(jq -r '.config.raw_topic // empty' "$CAPTURE_FILE")"
[[ "$MODE" == "MINIO_KAFKA" ]] || fail "Catalogue $GID change capture mode is '$MODE', expected MINIO_KAFKA"
[[ "$ENABLED" == "true" ]] || fail "Catalogue $GID change capture is disabled"
[[ -n "$RAW_TOPIC" ]] || fail "Catalogue $GID has no config.raw_topic; configure change capture first"

NORMALIZED_TOPIC="amp.storage.changes"

kafka_total() {
  local topic="$1"
  docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T kafka \
    /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server kafka:9092 --topic "$topic" 2>/dev/null \
    | awk -F: '{sum += $3} END {print sum+0}'
}

consumer_lag() {
  local group="${1:-amp-catalogue-events}"
  docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T kafka \
    /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --describe --group "$group" 2>/dev/null \
    | awk 'NR>1 && $6 ~ /^[0-9]+$/ {sum += $6} END {print sum+0}'
}

failed_test_events() {
  local lim=$((TOTAL + UPDATE_COUNT + DELETE_COUNT + 500))
  curl -fsSG "$BASE/api/v1/storage-events" \
    --data-urlencode "tenant_id=$TENANT" \
    --data-urlencode "catalogue_group_id=$GID" \
    --data-urlencode "limit=$lim" 2>/dev/null \
    | jq --arg p "$PREFIX" '[.[] | select((.object_key // "") | startswith($p)) | select(.status=="FAILED")] | length' 2>/dev/null \
    || echo 0
}

catalogue_json() {
  local out rc
  out="$(curl -fsSG "$BASE/api/v1/catalogue-objects" \
    --data-urlencode "tenant_id=$TENANT" \
    --data-urlencode "catalogue_group_id=$GID" \
    --data-urlencode "q=$PREFIX" \
    --data-urlencode "limit=$LIMIT")" || return $?

  if ! jq -e 'type == "array"' >/dev/null 2>&1 <<<"$out"; then
    echo "[debug] Unexpected catalogue object response: $out" >&2
    return 22
  fi
  printf '%s\n' "$out"
}

wait_for_counts() {
  local want_active="$1" want_tomb="$2" label="$3"
  local deadline=$((SECONDS + TIMEOUT))
  local started=$SECONDS
  local last_total=-1
  local last_progress=$SECONDS

  while (( SECONDS < deadline )); do
    if catalogue_json >"$TMP/current.json" 2>/dev/null; then
      local total active tomb lag raw_now norm_now raw_delta norm_delta failed elapsed
      total="$(jq 'length' "$TMP/current.json")"
      active="$(jq '[.[] | select(.lifecycle_state=="ACTIVE")] | length' "$TMP/current.json")"
      tomb="$(jq '[.[] | select(.lifecycle_state=="TOMBSTONED")] | length' "$TMP/current.json")"
      lag="$(consumer_lag amp-catalogue-events)"
      raw_now="$(kafka_total "$RAW_TOPIC")"
      norm_now="$(kafka_total "$NORMALIZED_TOPIC")"
      raw_delta=$((raw_now - RAW_START))
      norm_delta=$((norm_now - NORM_START))
      failed="$(failed_test_events)"
      elapsed=$((SECONDS - started))

      if (( total > last_total )); then
        last_total=$total
        last_progress=$SECONDS
      fi

      printf '\r    %-22s total=%-4s active=%-4s tomb=%-3s kafkaLag=%-4s rawΔ=%-4s normΔ=%-4s failed=%-3s elapsed=%ss' \
        "$label" "$total" "$active" "$tomb" "$lag" "$raw_delta" "$norm_delta" "$failed" "$elapsed"

      if (( total == TOTAL && active == want_active && tomb == want_tomb )); then
        echo
        return 0
      fi
    fi
    sleep "$POLL_SECONDS"
  done

  echo
  local lag raw_now norm_now failed
  lag="$(consumer_lag amp-catalogue-events)"
  raw_now="$(kafka_total "$RAW_TOPIC")"
  norm_now="$(kafka_total "$NORMALIZED_TOPIC")"
  failed="$(failed_test_events)"
  echo "[diagnostic] timeout after ${TIMEOUT}s" >&2
  echo "[diagnostic] catalogue rows seen: ${last_total}/${TOTAL}" >&2
  echo "[diagnostic] amp-catalogue-events lag: $lag" >&2
  echo "[diagnostic] raw Kafka delta: $((raw_now - RAW_START))" >&2
  echo "[diagnostic] normalized Kafka delta: $((norm_now - NORM_START))" >&2
  echo "[diagnostic] failed test storage events: $failed" >&2
  if (( lag > 0 )); then
    echo "[diagnostic] Events are still queued. This is backpressure/throughput, not necessarily event loss." >&2
    echo "[diagnostic] Re-run with AMP_TEST_TIMEOUT=1800 or scale catalogue consumers for load testing." >&2
  elif (( failed > 0 )); then
    echo "[diagnostic] Kafka is drained but some events failed; inspect amp-events logs and the DLQ." >&2
  else
    echo "[diagnostic] Kafka is drained with no failed events but expected catalogue state is missing; inspect catalogue idempotency/routing." >&2
  fi
  return 1
}

RAW_START="$(kafka_total "$RAW_TOPIC")"
NORM_START="$(kafka_total "$NORMALIZED_TOPIC")"

cat <<SUMMARY
AMP Catalogue lifecycle integration test
---------------------------------------
Catalogue group : $GID
Container       : $CONTAINER
Raw topic       : $RAW_TOPIC
Test prefix     : $PREFIX
Objects         : $TOTAL
Updates         : $UPDATE_COUNT
Deletes         : $DELETE_COUNT
Timeout         : ${TIMEOUT}s/phase
SUMMARY

info "Phase 1/5 - creating $TOTAL objects directly in source storage (bypassing AMP)"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T \
  -e AMP_TEST_PREFIX="$PREFIX" -e AMP_TEST_TOTAL="$TOTAL" amp python - <<'PY'
import json, os
from concurrent.futures import ThreadPoolExecutor
import boto3
from botocore.config import Config

prefix=os.environ['AMP_TEST_PREFIX']
total=int(os.environ['AMP_TEST_TOTAL'])
endpoint=os.environ['AMP_LAB_LEGACY_ENDPOINT']
bucket=os.environ['AMP_LAB_LEGACY_BUCKET']
access=os.environ['AMP_LAB_LEGACY_ACCESS_KEY']
secret=os.environ['AMP_LAB_LEGACY_SECRET_KEY']

def client():
    return boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access,
                        aws_secret_access_key=secret, region_name='us-east-1',
                        config=Config(s3={'addressing_style':'path'}, retries={'max_attempts':5}))

def put(i):
    key=f"{prefix}object-{i:06d}.json"
    body=json.dumps({
        'ampCatalogueLifecycleTest': True,
        'sequence': i,
        'revision': 1,
        'message': f'created outside AMP object {i}'
    }, sort_keys=True).encode()
    c=client()
    c.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json',
                 Metadata={'amp-test':'catalogue-lifecycle','revision':'1'})
    return key

with ThreadPoolExecutor(max_workers=16) as ex:
    for n, _ in enumerate(ex.map(put, range(1,total+1)), 1):
        if n % 100 == 0 or n == total:
            print(f"  created {n}/{total}", flush=True)
PY

wait_for_counts "$TOTAL" 0 "waiting for creates" || fail "Create events did not converge to $TOTAL ACTIVE objects"
ok "$TOTAL externally-created objects reached AMP Catalogue"
catalogue_json >"$TMP/created.json"

jq --argjson n "$UPDATE_COUNT" 'sort_by(.object_key) | .[0:$n] | map({object_key,recon_id,etag,id,virtual_shard,shard_id})' \
  "$TMP/created.json" >"$TMP/update-before.json"
jq --argjson u "$UPDATE_COUNT" --argjson d "$DELETE_COUNT" 'sort_by(.object_key) | .[$u:($u+$d)] | map({object_key,recon_id,etag,id})' \
  "$TMP/created.json" >"$TMP/delete-before.json"

SHARD_COUNT="$(jq '[.[].shard_id] | unique | length' "$TMP/created.json")"
(( SHARD_COUNT >= 2 || TOTAL < 10 )) || fail "Expected test objects to distribute across multiple physical shards; saw $SHARD_COUNT"
ok "hash routing distributed objects across $SHARD_COUNT physical shards"

info "Phase 2/5 - updating $UPDATE_COUNT objects directly in source storage"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T \
  -e AMP_TEST_PREFIX="$PREFIX" -e AMP_TEST_UPDATES="$UPDATE_COUNT" amp python - <<'PY'
import json, os
from concurrent.futures import ThreadPoolExecutor
import boto3
from botocore.config import Config

prefix=os.environ['AMP_TEST_PREFIX']; count=int(os.environ['AMP_TEST_UPDATES'])
endpoint=os.environ['AMP_LAB_LEGACY_ENDPOINT']; bucket=os.environ['AMP_LAB_LEGACY_BUCKET']
access=os.environ['AMP_LAB_LEGACY_ACCESS_KEY']; secret=os.environ['AMP_LAB_LEGACY_SECRET_KEY']

def client():
    return boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access,
                        aws_secret_access_key=secret, region_name='us-east-1',
                        config=Config(s3={'addressing_style':'path'}, retries={'max_attempts':5}))

def put(i):
    key=f"{prefix}object-{i:06d}.json"
    body=json.dumps({
        'ampCatalogueLifecycleTest': True,
        'sequence': i,
        'revision': 2,
        'message': f'UPDATED outside AMP object {i}',
        'padding': 'x' * 256
    }, sort_keys=True).encode()
    c=client(); c.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json',
                             Metadata={'amp-test':'catalogue-lifecycle','revision':'2'})

with ThreadPoolExecutor(max_workers=12) as ex:
    list(ex.map(put, range(1,count+1)))
print(f"  updated {count}/{count}")
PY

# Wait until all selected objects have a new ETag while keeping the same reconciliation identity.
deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  catalogue_json >"$TMP/current.json"
  if python3 - "$TMP/update-before.json" "$TMP/current.json" <<'PY'
import json,sys
before={x['object_key']:x for x in json.load(open(sys.argv[1]))}
current={x['object_key']:x for x in json.load(open(sys.argv[2]))}
for key,b in before.items():
    c=current.get(key)
    if not c or c.get('lifecycle_state')!='ACTIVE' or c.get('etag')==b.get('etag'):
        raise SystemExit(1)
raise SystemExit(0)
PY
  then break; fi
  sleep "$POLL_SECONDS"
done
catalogue_json >"$TMP/after-update.json"
python3 - "$TMP/update-before.json" "$TMP/after-update.json" <<'PY'
import json,sys
before={x['object_key']:x for x in json.load(open(sys.argv[1]))}
after={x['object_key']:x for x in json.load(open(sys.argv[2]))}
errors=[]
for key,b in before.items():
    a=after.get(key)
    if not a: errors.append(f"missing catalogue row: {key}"); continue
    if a.get('recon_id') != b.get('recon_id'): errors.append(f"recon ID changed: {key}")
    if a.get('etag') == b.get('etag'): errors.append(f"ETag did not change: {key}")
    if a.get('lifecycle_state') != 'ACTIVE': errors.append(f"not ACTIVE after update: {key}")
    if a.get('source_mode') != 'EVENT': errors.append(f"source_mode not EVENT: {key}")
if errors:
    print('\n'.join(errors[:20]), file=sys.stderr); raise SystemExit(1)
print(f"validated {len(before)} updates: stable recon IDs + refreshed ETags")
PY
ok "$UPDATE_COUNT updates preserved reconciliation identity and refreshed storage state"

info "Phase 3/5 - deleting $DELETE_COUNT different objects directly in source storage"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T \
  -e AMP_TEST_PREFIX="$PREFIX" -e AMP_TEST_START="$((UPDATE_COUNT+1))" -e AMP_TEST_DELETES="$DELETE_COUNT" amp python - <<'PY'
import os
from concurrent.futures import ThreadPoolExecutor
import boto3
from botocore.config import Config

prefix=os.environ['AMP_TEST_PREFIX']; start=int(os.environ['AMP_TEST_START']); count=int(os.environ['AMP_TEST_DELETES'])
endpoint=os.environ['AMP_LAB_LEGACY_ENDPOINT']; bucket=os.environ['AMP_LAB_LEGACY_BUCKET']
access=os.environ['AMP_LAB_LEGACY_ACCESS_KEY']; secret=os.environ['AMP_LAB_LEGACY_SECRET_KEY']

def client():
    return boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access,
                        aws_secret_access_key=secret, region_name='us-east-1',
                        config=Config(s3={'addressing_style':'path'}, retries={'max_attempts':5}))

def delete(i):
    client().delete_object(Bucket=bucket, Key=f"{prefix}object-{i:06d}.json")

with ThreadPoolExecutor(max_workers=12) as ex:
    list(ex.map(delete, range(start,start+count)))
print(f"  deleted {count}/{count}")
PY

EXPECTED_ACTIVE=$((TOTAL - DELETE_COUNT))
wait_for_counts "$EXPECTED_ACTIVE" "$DELETE_COUNT" "waiting for deletes" \
  || fail "Delete events did not converge to $EXPECTED_ACTIVE ACTIVE / $DELETE_COUNT TOMBSTONED"
ok "$DELETE_COUNT delete events produced immediate tombstones"
catalogue_json >"$TMP/final.json"

python3 - "$TMP/delete-before.json" "$TMP/final.json" <<'PY'
import json,sys
before={x['object_key']:x for x in json.load(open(sys.argv[1]))}
after={x['object_key']:x for x in json.load(open(sys.argv[2]))}
errors=[]
for key,b in before.items():
    a=after.get(key)
    if not a: errors.append(f"missing tombstone row: {key}"); continue
    if a.get('recon_id') != b.get('recon_id'): errors.append(f"delete changed recon ID: {key}")
    if a.get('lifecycle_state') != 'TOMBSTONED': errors.append(f"not TOMBSTONED: {key}")
    if int(a.get('missing_count') or 0) < 2: errors.append(f"tombstone missing_count invalid: {key}")
if errors:
    print('\n'.join(errors[:20]), file=sys.stderr); raise SystemExit(1)
print(f"validated {len(before)} tombstones with preserved recon IDs")
PY

info "Phase 4/5 - validating source-payload access and event health"
UPDATED_ID="$(jq -r 'sort_by(.object_key) | .[0].id' "$TMP/final.json")"
DELETED_ID="$(jq -r --argjson u "$UPDATE_COUNT" 'sort_by(.object_key) | .[$u].id' "$TMP/final.json")"

STATUS="$(curl -sS -D "$TMP/headers.txt" -o "$TMP/payload.json" -w '%{http_code}' "$BASE/api/v1/catalogue-objects/$UPDATED_ID/content")"
[[ "$STATUS" == "200" ]] || fail "Updated source payload returned HTTP $STATUS"
grep -qi '^x-amp-recon-id:' "$TMP/headers.txt" || fail "Payload response missing x-amp-recon-id header"
grep -qi '^x-amp-catalogue:' "$TMP/headers.txt" || fail "Payload response missing x-amp-catalogue header"
python3 - "$TMP/payload.json" <<'PY'
import json,sys
x=json.load(open(sys.argv[1]))
assert x['revision']==2, x
assert 'UPDATED outside AMP' in x['message'], x
PY
ok "Open source payload returns the updated authoritative object"

DELETE_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/v1/catalogue-objects/$DELETED_ID/content")"
[[ "$DELETE_STATUS" == "404" ]] || fail "Tombstoned object payload expected HTTP 404, got $DELETE_STATUS"
ok "Tombstoned object no longer exposes a source payload"

EVENT_LIMIT=$((TOTAL + UPDATE_COUNT + DELETE_COUNT + 200))
curl -fsSG "$BASE/api/v1/storage-events" \
  --data-urlencode "tenant_id=$TENANT" --data-urlencode "catalogue_group_id=$GID" --data-urlencode "limit=$EVENT_LIMIT" \
  >"$TMP/events.json"
FAILED_EVENTS="$(jq --arg p "$PREFIX" '[.[] | select((.object_key // "") | startswith($p)) | select(.status=="FAILED")] | length' "$TMP/events.json")"
[[ "$FAILED_EVENTS" == "0" ]] || fail "$FAILED_EVENTS test storage events are FAILED"
ok "no failed storage events for this test run"

RAW_END="$(kafka_total "$RAW_TOPIC")"
NORM_END="$(kafka_total "$NORMALIZED_TOPIC")"
EXPECTED_EVENTS=$((TOTAL + UPDATE_COUNT + DELETE_COUNT))
RAW_DELTA=$((RAW_END - RAW_START))
NORM_DELTA=$((NORM_END - NORM_START))
(( RAW_DELTA >= EXPECTED_EVENTS )) || fail "Raw Kafka event delta $RAW_DELTA < expected $EXPECTED_EVENTS"
(( NORM_DELTA >= EXPECTED_EVENTS )) || fail "Normalized Kafka event delta $NORM_DELTA < expected $EXPECTED_EVENTS"
ok "Kafka observed >= $EXPECTED_EVENTS source and normalized events (raw=$RAW_DELTA normalized=$NORM_DELTA)"

info "Phase 5/5 - final invariants"
TOTAL_FINAL="$(jq 'length' "$TMP/final.json")"
ACTIVE_FINAL="$(jq '[.[]|select(.lifecycle_state=="ACTIVE")]|length' "$TMP/final.json")"
TOMB_FINAL="$(jq '[.[]|select(.lifecycle_state=="TOMBSTONED")]|length' "$TMP/final.json")"
EVENT_MODE="$(jq '[.[]|select(.source_mode=="EVENT")]|length' "$TMP/final.json")"
[[ "$TOTAL_FINAL" == "$TOTAL" ]] || fail "Expected $TOTAL catalogue rows, got $TOTAL_FINAL"
[[ "$ACTIVE_FINAL" == "$EXPECTED_ACTIVE" ]] || fail "Expected $EXPECTED_ACTIVE ACTIVE, got $ACTIVE_FINAL"
[[ "$TOMB_FINAL" == "$DELETE_COUNT" ]] || fail "Expected $DELETE_COUNT TOMBSTONED, got $TOMB_FINAL"
[[ "$EVENT_MODE" == "$TOTAL" ]] || fail "Expected all $TOTAL rows source_mode=EVENT, got $EVENT_MODE"

cat <<RESULT

PASS - AMP Catalogue external-object lifecycle
----------------------------------------------
Prefix                    $PREFIX
Catalogue rows            $TOTAL_FINAL
ACTIVE                    $ACTIVE_FINAL
TOMBSTONED                $TOMB_FINAL
Updated/revalidated       $UPDATE_COUNT
Recon IDs preserved       YES
Source payload verified   YES
External event mode       $EVENT_MODE/$TOTAL
Physical shards used      $SHARD_COUNT
Raw Kafka delta           $RAW_DELTA
Normalized Kafka delta    $NORM_DELTA
Failed storage events     $FAILED_EVENTS
RESULT

if [[ "$CLEANUP" == "1" ]]; then
  info "Cleanup requested - deleting remaining ACTIVE test objects"
  docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T \
    -e AMP_TEST_PREFIX="$PREFIX" -e AMP_TEST_TOTAL="$TOTAL" -e AMP_TEST_ALREADY_DELETED_START="$((UPDATE_COUNT+1))" \
    -e AMP_TEST_ALREADY_DELETED_COUNT="$DELETE_COUNT" amp python - <<'PY'
import os
from concurrent.futures import ThreadPoolExecutor
import boto3
from botocore.config import Config
prefix=os.environ['AMP_TEST_PREFIX']; total=int(os.environ['AMP_TEST_TOTAL'])
start=int(os.environ['AMP_TEST_ALREADY_DELETED_START']); count=int(os.environ['AMP_TEST_ALREADY_DELETED_COUNT'])
skip=set(range(start,start+count))
endpoint=os.environ['AMP_LAB_LEGACY_ENDPOINT']; bucket=os.environ['AMP_LAB_LEGACY_BUCKET']
access=os.environ['AMP_LAB_LEGACY_ACCESS_KEY']; secret=os.environ['AMP_LAB_LEGACY_SECRET_KEY']
def client():
    return boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                        region_name='us-east-1', config=Config(s3={'addressing_style':'path'}, retries={'max_attempts':5}))
def delete(i): client().delete_object(Bucket=bucket, Key=f"{prefix}object-{i:06d}.json")
with ThreadPoolExecutor(max_workers=16) as ex: list(ex.map(delete, [i for i in range(1,total+1) if i not in skip]))
PY
  wait_for_counts 0 "$TOTAL" "waiting for cleanup" || fail "Cleanup events did not tombstone all test objects"
  ok "cleanup completed; all $TOTAL test rows remain as audit tombstones"
else
  echo "Test data retained for UI inspection. Set AMP_TEST_CLEANUP=1 to tombstone all test objects after the run."
fi
