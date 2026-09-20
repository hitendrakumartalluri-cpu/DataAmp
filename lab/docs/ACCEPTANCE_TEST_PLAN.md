# AMP 0.9.0-beta.5.0 — Clean Lab Acceptance Plan

Run these tests **one at a time** from `~/projects/amp-enterprise-beta`. Do not continue after a failed step; inspect the first failure.

## 0. Unit/static validation

```bash
make test
```

Expected: all backend tests pass.

## 1. Lab preflight

```bash
./scripts/lab-preflight.sh
```

## 2. Start/bootstrap lab

```bash
./scripts/lab-up.sh
```

Expected: PostgreSQL, Kafka, Primary/Legacy MinIO, Solr, Tika, Hop and AMP services are running; both MinIO buckets have native versioning enabled.

## 3. Beta.5 readiness

```bash
./scripts/lab-beta5-readiness.sh
```

Confirms the running version, primary/legacy Catalogue IDs, raw-topic mappings and backend-native MinIO versioning.

## 4. Base smoke test

```bash
./scripts/lab-smoke-test.sh
```

## 5. External object change capture

```bash
./scripts/lab-event-smoke-test.sh
```

Proves direct storage PUT/DELETE -> MinIO notification -> Kafka -> AMP Catalogue without a discovery scan.

## 6. Catalogue lifecycle

Use a small run initially:

```bash
AMP_TEST_TOTAL=50 AMP_TEST_UPDATES=10 AMP_TEST_DELETES=5 AMP_TEST_TIMEOUT=900 \
./scripts/lab-catalogue-lifecycle-test.sh
```

Expected: stable recon IDs across updates and tombstones on deletes.

## 7. HCP REST direct ingest + sidecars

```bash
AMP_HCP_TEST_TOTAL=10 ./scripts/lab-hcp-rest-ingest-test.sh
```

Proves payload + default/legal/migration sidecars + manifest package layout.

## 8. Package placement policy

```bash
./scripts/lab-package-placement-test.sh
```

Proves `AMP_MANAGED_HASH` and `CLIENT_PATH` while preserving the same `<GUID>/payload`, `annotations/`, `manifest.json` interior.

## 9. S3/HCP interoperability

```bash
AMP_S3_TEST_TOTAL=10 ./scripts/lab-s3-interop-test.sh
```

Proves signed S3 PUT/GET/HEAD/LIST/range/metadata/tags and cross-protocol HCP<->S3 access.

## 10. Backend-native versions and annotation snapshots

```bash
./scripts/lab-native-version-test.sh
```

Proves:

- same AMP logical object/package across overwrites,
- backend creates native payload versions,
- AMP tracks native versions but does not prune/manage them,
- explicit old payload retrieval works,
- old payload versions resolve the annotation snapshot recorded for that version.

## 11. Gateway response policy

```bash
./scripts/lab-response-policy-test.sh
```

Proves `AMP_NORMALIZED`, `AMP_NORMALIZED_WITH_BACKEND`, `RAW_BACKEND`, safe backend-header exposure and raw backend transaction capture.

## 12. Migration + read-through hydration

```bash
./scripts/lab-migration-hydration-test.sh
```

Proves current logical object + annotations migrate to a target AMP package, source/target catalogues remain independent, and read-through hydration registers the target only after the target write succeeds.

## 13. Reconciliation

Use the UI or:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/reconciliation/run \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"demo","catalogue_group_id":"<GROUP-ID>","target":"ALL"}' | jq
```

## Current beta boundaries

Not production-certified yet. Deferred/high-hardening items include multipart S3, CopyObject, presigned URLs, real HCP native adapter/MQE integration, S3 Inventory/S3 Metadata ingestion, production HOP/Solr pipelines, OIDC/RBAC, WAN/performance testing and HA/failover.

Authentication is deliberately parked for this beta.
