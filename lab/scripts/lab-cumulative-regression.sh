#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-.env}"
BASE="${AMP_URL:-http://127.0.0.1:8080}"
TENANT="${AMP_REGRESSION_TENANT:-demo}"
PROFILE="${AMP_REGRESSION_PROFILE:-quick}"
RESET="${AMP_REGRESSION_RESET:-0}"
CONTINUE_ON_FAILURE="${AMP_REGRESSION_CONTINUE:-0}"
CLEANUP="${AMP_REGRESSION_CLEANUP:-0}"
SKIP_CSV="${AMP_REGRESSION_SKIP:-}"
RUN_ID="${AMP_REGRESSION_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
RESULT_DIR="${AMP_REGRESSION_RESULT_DIR:-$ROOT/.amp-test-results/$RUN_ID}"
RESULTS_TSV="$RESULT_DIR/results.tsv"
mkdir -p "$RESULT_DIR"
: > "$RESULTS_TSV"

case "$PROFILE" in
  quick)
    DEFAULT_CAT_TOTAL=20; DEFAULT_CAT_UPDATES=4; DEFAULT_CAT_DELETES=2; DEFAULT_CAT_TIMEOUT=600
    DEFAULT_HCP_TOTAL=3; DEFAULT_S3_TOTAL=3; DEFAULT_MIG_TIMEOUT=300 ;;
  full)
    DEFAULT_CAT_TOTAL=50; DEFAULT_CAT_UPDATES=10; DEFAULT_CAT_DELETES=5; DEFAULT_CAT_TIMEOUT=900
    DEFAULT_HCP_TOTAL=10; DEFAULT_S3_TOTAL=10; DEFAULT_MIG_TIMEOUT=600 ;;
  soak)
    DEFAULT_CAT_TOTAL=500; DEFAULT_CAT_UPDATES=50; DEFAULT_CAT_DELETES=25; DEFAULT_CAT_TIMEOUT=1800
    DEFAULT_HCP_TOTAL=100; DEFAULT_S3_TOTAL=100; DEFAULT_MIG_TIMEOUT=900 ;;
  *) echo "[FAIL] AMP_REGRESSION_PROFILE must be quick, full, or soak (got '$PROFILE')" >&2; exit 2 ;;
esac

CAT_TOTAL="${AMP_REGRESSION_CAT_TOTAL:-$DEFAULT_CAT_TOTAL}"
CAT_UPDATES="${AMP_REGRESSION_CAT_UPDATES:-$DEFAULT_CAT_UPDATES}"
CAT_DELETES="${AMP_REGRESSION_CAT_DELETES:-$DEFAULT_CAT_DELETES}"
CAT_TIMEOUT="${AMP_REGRESSION_CAT_TIMEOUT:-$DEFAULT_CAT_TIMEOUT}"
HCP_TOTAL="${AMP_REGRESSION_HCP_TOTAL:-$DEFAULT_HCP_TOTAL}"
S3_TOTAL="${AMP_REGRESSION_S3_TOTAL:-$DEFAULT_S3_TOTAL}"
MIG_TIMEOUT="${AMP_REGRESSION_MIG_TIMEOUT:-$DEFAULT_MIG_TIMEOUT}"
FINAL_LAG_TIMEOUT="${AMP_REGRESSION_FINAL_LAG_TIMEOUT:-180}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
AMP cumulative regression runner

Usage (from the lab directory):
  ./scripts/lab-cumulative-regression.sh

Common options:
  AMP_REGRESSION_PROFILE=quick|full|soak
  AMP_REGRESSION_RESET=1
  AMP_REGRESSION_CONTINUE=1
  AMP_REGRESSION_CLEANUP=1
  AMP_REGRESSION_SKIP=a,b,c
HELP
  exit 0
fi

need(){ command -v "$1" >/dev/null 2>&1 || { echo "[FAIL] missing required command: $1" >&2; exit 2; }; }
for c in bash curl jq docker awk sed tee python3; do need "$c"; done
docker compose version >/dev/null 2>&1 || { echo "[FAIL] docker compose is required" >&2; exit 2; }

API_KEY="${AMP_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$ENV_FILE" ]]; then
  API_KEY="$(awk -F= '$1=="AMP_API_KEY"{sub(/^AMP_API_KEY=/,"");print;exit}' "$ENV_FILE" 2>/dev/null || true)"
fi
AUTH=(); [[ -n "$API_KEY" ]] && AUTH=(-H "x-api-key: $API_KEY")

PRIMARY_CAT=""; LEGACY_CAT=""; RUN_VERSION="unknown"; DLQ_START=0; FAILED_EVENTS_START=0

is_skipped(){ local name="$1"; [[ -z "$SKIP_CSV" ]] && return 1; case ",$SKIP_CSV," in *",$name,"*) return 0;; *) return 1;; esac; }
record_result(){ printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$RESULTS_TSV"; }

make_summary(){
  python3 - "$RESULTS_TSV" "$RESULT_DIR/summary.json" "$RUN_ID" "$PROFILE" "$RUN_VERSION" <<'PY'
import json,sys
src,out,run_id,profile,version=sys.argv[1:]
rows=[]
with open(src,encoding='utf-8') as f:
    for line in f:
        line=line.rstrip('\n')
        if not line: continue
        name,status,duration,log=line.split('\t',3)
        rows.append({'stage':name,'status':status,'duration_seconds':int(duration),'log':log})
payload={'run_id':run_id,'profile':profile,'amp_version':version,
         'passed':sum(r['status']=='PASS' for r in rows),
         'failed':sum(r['status']=='FAIL' for r in rows),
         'skipped':sum(r['status']=='SKIP' for r in rows),'stages':rows}
with open(out,'w',encoding='utf-8') as f: json.dump(payload,f,indent=2)
PY
  {
    echo "AMP cumulative regression summary"; echo "================================="
    echo "Run ID      : $RUN_ID"; echo "Profile     : $PROFILE"; echo "AMP version : $RUN_VERSION"
    echo "Results dir : $RESULT_DIR"; echo
    printf '%-28s %-8s %-10s %s\n' "STAGE" "STATUS" "SECONDS" "LOG"
    while IFS=$'\t' read -r name status duration log; do
      printf '%-28s %-8s %-10s %s\n' "$name" "$status" "$duration" "$log"
    done < "$RESULTS_TSV"
  } | tee "$RESULT_DIR/summary.txt"
}

finish_and_exit(){
  make_summary
  local failures; failures="$(awk -F'\t' '$2=="FAIL"{n++} END{print n+0}' "$RESULTS_TSV")"
  if (( failures > 0 )); then echo "[FAIL] cumulative regression completed with $failures failed stage(s)."; exit 1; fi
  echo "PASS - AMP cumulative regression"; echo "Results: $RESULT_DIR"
}
trap 'echo "[FAIL] regression interrupted" >&2; make_summary || true; exit 130' INT TERM

run_stage(){
  local name="$1"; shift; local description="$1"; shift
  local idx log started ended rc duration
  idx=$(printf '%02d' $(( $(wc -l < "$RESULTS_TSV") + 1 )))
  log="$RESULT_DIR/${idx}-${name}.log"
  if is_skipped "$name"; then record_result "$name" "SKIP" 0 "$log"; return 0; fi
  echo; echo "==> [$name] $description"; echo "    log: $log"
  started=$(date +%s); set +e; "$@" > >(tee "$log") 2>&1; rc=$?; set -e
  ended=$(date +%s); duration=$((ended-started))
  if (( rc == 0 )); then record_result "$name" "PASS" "$duration" "$log"; echo "[PASS] $name (${duration}s)"; return 0; fi
  record_result "$name" "FAIL" "$duration" "$log"; echo "[FAIL] $name (${duration}s, rc=$rc)" >&2
  [[ "$CONTINUE_ON_FAILURE" == "1" ]] || finish_and_exit
}

kafka_total(){ docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T kafka /opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server kafka:9092 --topic "$1" 2>/dev/null | awk -F: '{sum += $3} END {print sum+0}'; }
consumer_lag(){ docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --describe --group "$1" 2>/dev/null | awk 'NR>1 && $6 ~ /^[0-9]+$/ {sum += $6} END {print sum+0}'; }
failed_event_count(){
  local body
  body="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/storage-events" --data-urlencode "tenant_id=$TENANT" --data-urlencode "limit=5000" 2>/dev/null || echo '[]')"
  jq '[.[] | select(.status=="FAILED")] | length' <<<"$body" 2>/dev/null || echo 0
}
shell_syntax_check(){ local f; for f in scripts/*.sh; do bash -n "$f" || return 1; done; echo "[ok] all shell scripts pass bash -n"; }
wait_for_amp(){ local deadline=$((SECONDS+300)); while ((SECONDS<deadline)); do curl -fsS "$BASE/healthz" >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }
reset_lab(){ ./scripts/lab-reset.sh && wait_for_amp; }

readiness_check(){
  local health groups captures
  health="$(curl -fsS "$BASE/healthz")" || return 1
  RUN_VERSION="$(jq -r '.version // "unknown"' <<<"$health")"
  [[ "$(jq -r '.status // ""' <<<"$health")" == "ok" ]] || return 1
  [[ "$RUN_VERSION" == 0.9.0-beta.6.* ]] || { echo "expected beta.6.x development build, got $RUN_VERSION" >&2; return 1; }
  groups="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/catalogue-groups" --data-urlencode "tenant_id=$TENANT")" || return 1
  [[ "$(jq -r type <<<"$groups")" == "array" ]] || return 1
  PRIMARY_CAT="$(jq -r '.[] | select(.container_name=="amp-primary" and .state=="ACTIVE") | .id' <<<"$groups" | head -1)"
  LEGACY_CAT="$(jq -r '.[] | select(.container_name=="legacy-hcp" and .state=="ACTIVE") | .id' <<<"$groups" | head -1)"
  [[ -n "$PRIMARY_CAT" && -n "$LEGACY_CAT" ]] || return 1
  captures="$(curl -fsS "${AUTH[@]}" -G "$BASE/api/v1/change-capture" --data-urlencode "tenant_id=$TENANT")" || return 1
  [[ "$(jq -r --arg g "$PRIMARY_CAT" '.[]|select(.catalogue_group_id==$g)|.config.raw_topic' <<<"$captures")" == "amp.raw.minio.primary" ]] || return 1
  [[ "$(jq -r --arg g "$LEGACY_CAT" '.[]|select(.catalogue_group_id==$g)|.config.raw_topic' <<<"$captures")" == "amp.raw.minio.legacy" ]] || return 1
  docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T amp python - <<'PY'
import os,boto3
from botocore.config import Config
for label,prefix in [('primary','PRIMARY'),('legacy','LEGACY')]:
    c=boto3.client('s3', endpoint_url=os.environ[f'AMP_LAB_{prefix}_ENDPOINT'],
        aws_access_key_id=os.environ[f'AMP_LAB_{prefix}_ACCESS_KEY'],
        aws_secret_access_key=os.environ[f'AMP_LAB_{prefix}_SECRET_KEY'],
        region_name='us-east-1', config=Config(s3={'addressing_style':'path'}))
    b=os.environ[f'AMP_LAB_{prefix}_BUCKET']
    assert c.get_bucket_versioning(Bucket=b).get('Status','')=='Enabled'
PY
}

snapshot_baseline(){ DLQ_START="$(kafka_total amp.storage.changes.dlq)"; FAILED_EVENTS_START="$(failed_event_count)"; }

final_event_health(){
  local deadline=$((SECONDS+FINAL_LAG_TIMEOUT)) c p l dlq_end failed_end
  while ((SECONDS<deadline)); do
    c="$(consumer_lag amp-catalogue-events)"; p="$(consumer_lag amp-minio-primary-adapter)"; l="$(consumer_lag amp-minio-legacy-adapter)"
    (( c==0 && p==0 && l==0 )) && break
    sleep 2
  done
  c="$(consumer_lag amp-catalogue-events)"; p="$(consumer_lag amp-minio-primary-adapter)"; l="$(consumer_lag amp-minio-legacy-adapter)"
  (( c==0 && p==0 && l==0 )) || return 1
  dlq_end="$(kafka_total amp.storage.changes.dlq)"; failed_end="$(failed_event_count)"
  (( dlq_end <= DLQ_START )) || return 1
  (( failed_end <= FAILED_EVENTS_START )) || return 1
}

echo "AMP cumulative regression — profile=$PROFILE run=$RUN_ID"
run_stage shell-syntax "Validate all lab shell scripts" shell_syntax_check
run_stage preflight "Host/Docker lab prerequisites" ./scripts/lab-preflight.sh
[[ "$RESET" == "1" ]] && run_stage reset "Destroy and rebuild the complete lab" reset_lab
run_stage readiness "Validate beta.6 development health, catalogues, change capture and backend-native versioning" readiness_check
run_stage baseline "Capture DLQ and FAILED-event baselines" snapshot_baseline
run_stage smoke "Base API smoke + basic reconciliation invocation" ./scripts/lab-smoke-test.sh
run_stage event-smoke "External MinIO create/delete -> Kafka -> Catalogue" ./scripts/lab-event-smoke-test.sh
run_stage catalogue-lifecycle "External create/update/delete with stable reconciliation identity" env AMP_TEST_TOTAL="$CAT_TOTAL" AMP_TEST_UPDATES="$CAT_UPDATES" AMP_TEST_DELETES="$CAT_DELETES" AMP_TEST_TIMEOUT="$CAT_TIMEOUT" AMP_TEST_CLEANUP="$CLEANUP" ./scripts/lab-catalogue-lifecycle-test.sh
run_stage hcp-rest "HCP REST ingest + 3 annotations + package isolation" env AMP_HCP_TEST_CATALOGUE_GROUP_ID="$PRIMARY_CAT" AMP_HCP_TEST_TOTAL="$HCP_TOTAL" AMP_HCP_TEST_CLEANUP="$CLEANUP" ./scripts/lab-hcp-rest-ingest-test.sh
run_stage package-placement "AMP_MANAGED_HASH and CLIENT_PATH physical package placement" env AMP_PACKAGE_TEST_CATALOGUE_GROUP_ID="$PRIMARY_CAT" ./scripts/lab-package-placement-test.sh
run_stage s3-interop "S3 SigV4 + range/list/metadata/tags + HCP<->S3 interoperability" env AMP_S3_TEST_CATALOGUE_GROUP_ID="$PRIMARY_CAT" AMP_S3_TEST_TOTAL="$S3_TOTAL" AMP_S3_TEST_CLEANUP="$CLEANUP" ./scripts/lab-s3-interop-test.sh
run_stage native-version "Backend-native payload/annotation versions and historical retrieval" env AMP_VERSION_TEST_CATALOGUE_GROUP_ID="$PRIMARY_CAT" ./scripts/lab-native-version-test.sh
run_stage response-policy "RAW_BACKEND and normalized Gateway response policies" env AMP_RESPONSE_TEST_CATALOGUE_GROUP_ID="$PRIMARY_CAT" ./scripts/lab-response-policy-test.sh
run_stage migration-hydration "Migration dry-run/copy and read-through hydration" env AMP_MIG_TEST_TIMEOUT="$MIG_TIMEOUT" ./scripts/lab-migration-hydration-test.sh
run_stage storage-reconciliation "Targeted, tally and full Storage↔Catalogue reconciliation with controlled drift" env AMP_RECON_TEST_CATALOGUE_GROUP_ID="$PRIMARY_CAT" ./scripts/lab-reconciliation-test.sh
run_stage final-event-health "Ensure Kafka drained and no new DLQ/FAILED events were introduced" final_event_health
finish_and_exit
