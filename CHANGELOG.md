# Changelog

## 0.9.0-beta.5.0 - backend-authoritative Gateway consolidation

- Locked the product boundary: backend storage owns native versioning, WORM, retention, legal hold/Object Lock, lifecycle, pruning, durability and compliance decisions. AMP observes/translates/reconciles only.
- Stable logical object/recon identity across backend-native versions; overwrites reuse the same package GUID and payload/annotation keys.
- Added native payload version catalogue and explicit native-version GET/HEAD support.
- Added annotation native-version catalogue plus payload-version -> annotation-version snapshot links for historic HCP-style annotation reads.
- Added per-Gateway-Route response policy: `RAW_BACKEND`, `AMP_NORMALIZED`, `AMP_NORMALIZED_WITH_BACKEND`.
- Added backend header policies: `NONE`, `SELECTED`, `ALL_SAFE`, optional AMP request ID and bounded backend response capture.
- Added backend transaction history for success/failure diagnostics.
- Added backend-reported compliance-state visibility without AMP enforcement.
- Reworked migration/hydration to create target `AMP_PACKAGE_V3` packages and copy managed annotations instead of treating physical package members as business objects.
- Lab bootstrap enables native MinIO bucket versioning.
- Added native-version, response-policy and migration/hydration acceptance tests.
- Added Gateway response-policy UI and native version/compliance visibility in Catalogue object detail.
- Authentication federation is intentionally parked for a later beta.

## 0.9.0-beta.4.8 - configurable package placement

- Adds configurable managed package placement per Catalogue Group.
- `AMP_MANAGED_HASH` uses `.amp/objects/<2hex>/<2hex>/<GUID>/` by default.
- `CLIENT_PATH` preserves the client REST/S3 logical path and appends `<GUID>/`.
- Both modes retain the identical package interior: `payload`, `annotations/`, `manifest.json`.
- Introduces `AMP_PACKAGE_V3` manifests with placement mode and package ID.
- Adds package-layout API/UI controls; changes apply only to future managed writes.
- Discovery, HOP simulation and native event filtering understand both V3 placement modes and remain backward compatible with V1/V2 packages.
- Adds automated package-placement integration test.

## 0.9.0-beta.4.7 - protocol-neutral ingest + S3-compatible front door

- Introduces a canonical `ManagedObjectService` shared by HCP REST and S3 client adapters.
- Replaces new managed-object physical layout with `AMP_PACKAGE_V2`: `.amp/objects/<deterministic-package-id>/{payload,annotations/*,manifest.json}`.
- Keeps the original client object key purely logical, preventing collisions with arbitrary S3 keys such as `foo/payload` or `foo/annotations/legal`.
- Adds path-style S3 PutObject/GetObject/HeadObject/DeleteObject/ListObjectsV2, Range GET and GetObjectTagging.
- Adds AWS Signature V4 Authorization-header validation for the lab/pilot S3 front door.
- Maps `x-amz-meta-*` and `x-amz-tagging` to managed sidecar annotations.
- Adds cross-protocol interoperability: HCP REST writes can be read through S3 and S3 writes can be read through HCP REST while retaining one logical Catalogue identity.
- Discovery and HOP simulator understand both AMP_PACKAGE_V1 and AMP_PACKAGE_V2 manifests.
- Adds `lab/scripts/lab-s3-interop-test.sh` and `make lab-s3-interop-test`.
- Core automated suite: 14 passing tests, plus a live boto3/SigV4 smoke validation.


## 0.9.0-beta.4.6 - AMP object-package sidecar layout

- HCP-style Gateway objects now use an object-package layout: `<logical-object>/payload`, `<logical-object>/annotations/*`, and `<logical-object>/.amp/manifest.json`.
- Business catalogue rows track the logical object while `payload_key`, `package_root`, `manifest_key`, and `storage_layout` record physical layout.
- PostgreSQL/SQLite schema evolution preserves existing beta catalogues and backfills legacy `payload_key=object_key`.
- Annotation sidecars preserve content type with `.json`, `.xml`, `.csv`, `.txt`, or `.bin` extensions.
- Package manifests make managed Gateway objects reconstructable from storage without PostgreSQL.
- Discovery and the HOP simulator recognize package manifests and do not catalogue/index payload, annotation, or manifest members independently.
- Native storage events for package members are retained for audit as `IGNORED_SYSTEM`.
- Catalogue UI now exposes storage layout, physical payload key, and manifest key.
- Automated HCP REST test verifies 10 logical objects, 30 annotation sidecars, 10 payload members, 10 manifests, API round-trip and zero package-member catalogue leakage.
- Core test suite: 12 passing tests.

# AMP Enterprise Beta Changelog

## 0.9.0-beta.4.5 - HCP REST direct ingestion + annotation sidecars

- Adds HCP-style PUT/GET/HEAD/DELETE object API under `/rest/{tenant}/{namespace}/{path}`.
- Adds explicit HCP namespace → AMP Catalogue Group routing.
- Adds HCP custom-metadata annotation PUT/GET/HEAD/DELETE support.
- Stores annotation bodies as managed `.amp/annotations/...` storage sidecars.
- Adds annotation manifest rows with checksum, size, content type and independent annotation versions.
- Reserved `.amp/` objects are excluded from catalogue discovery, HOP indexing and business-object event mutation.
- Gateway-origin objects retain `source_mode=GATEWAY` when storage notifications echo the write back through Kafka.
- Catalogue object detail now surfaces managed HCP-style annotations.
- Adds `lab/scripts/lab-hcp-rest-ingest-test.sh` (10 payloads × 3 annotations by default).
- Core automated suite: 11/11 passing.


## 0.9.0-beta.4.4.3 - Tombstone payload semantics hotfix

- Prevents source-storage GET attempts for catalogue rows already marked `TOMBSTONED`.
- Maps S3/MinIO `NoSuchKey`/HTTP 404 reads to `FileNotFoundError`, allowing the API to return a controlled 404 instead of 502.
- Disables the **Open source payload** action for tombstoned catalogue objects and explains that the tombstone is retained for audit/reconciliation.
- Adds a regression test for tombstoned payload access.
## 0.9.0-beta.4.4 - Catalogue lifecycle integration automation

- Added `lab/scripts/lab-catalogue-lifecycle-test.sh` for repeatable external-object lifecycle testing.
- Automates direct source-storage create/update/delete operations without using the AMP Gateway.
- Validates MinIO → Kafka → normalized event → targeted Catalogue upsert/tombstone flow.
- Verifies deterministic Recon ID stability across in-place object updates and deletes.
- Verifies authoritative source-payload retrieval and tombstone payload behavior.
- Checks physical shard distribution, event failures, and raw/normalized Kafka event deltas.
- Added optional test cleanup and configurable object/update/delete counts.
- Added `make lab-catalogue-test` convenience target.

## 0.9.0-beta.4.3 - Payload retrieval hotfix

- Normalize PostgreSQL UUID catalogue/recon identifiers to strings before emitting HTTP response headers.
- Add inline Content-Disposition for source payload viewing.
- Convert unexpected source-storage read failures to controlled HTTP 502 responses and emit server-side diagnostic logs.
- Preserve 404 semantics for missing catalogue rows/payloads.

# 0.9.0-beta.3.2

- Fix browser stale-asset issue after catalogue-sharding upgrade.
- Add cache-busting query strings to UI CSS/JavaScript.
- Add no-store headers for beta UI/static assets so old frontend API calls are not reused after upgrades.

# Changelog

## 0.9.0-beta.3.1 — container-sharded catalogue rebuild

- Replaced the global multi-location object catalogue with one logical Catalogue Group per storage system + namespace/bucket/container.
- Added small Catalogue Registry control plane.
- Added 1024 stable virtual shards and configurable physical shard ranges.
- Added deterministic UUIDv5 recon IDs shared with HOP/Solr/AI.
- Added discovery generations, missing detection and tombstones.
- Added independent catalogue lifecycle states.
- Changed migration/hydration to create independent target catalogue records linked to source recon IDs.
- Removed catalogue-object dependency from the HOP/indexing lab path.
- Added storage/index/AI reconciliation per catalogue group and optional physical shard.
- Added index orphan detection.
- Reworked UI around Catalogue Groups and shard maps.
- Kept the MinIO Quay registry hotfix and explicit image-pull validation from beta.2.

## 0.9.0-beta.2

- Lab image registry/tag hotfix.

## 0.9.0-beta.1

- Initial enterprise beta and clean lab.

## 0.9.0-beta.4.3 — continuous catalogue change capture

- Added single-node Kafka KRaft broker to the full lab.
- Added storage-specific raw MinIO event topics and two independent MinIO adapters.
- Added shared normalized `amp.storage.changes` topic and catalogue consumer group.
- Added normalized storage-event schema for HCP/AWS/MinIO.
- Added AWS SQS/EventBridge-to-Kafka adapter.
- Added configurable HCP MQE operation poller with overlap checkpoint.
- Added targeted source HEAD verification before event-driven catalogue updates.
- Added event receipt ledger, idempotency, duplicate suppression and S3/MinIO sequencer watermarks.
- Added database and Kafka dead-letter paths plus replay APIs.
- Added per-catalogue change-capture state, checkpoint and lag.
- Added independent baseline, integrity-audit and reconciliation schedules plus scheduler worker.
- Updated Platform UI for change-capture configuration, event monitoring and catalogue schedules.
- Kept HOP independent from AMP Catalogue; recon IDs remain the common downstream validation key.
- Core automated test suite now 9/9 passing.

## 0.9.0-beta.4.3.3 hotfix
- Fix PostgreSQL JSON/JSONB decoding: psycopg returns native dict/list values, while the beta loader expected serialized strings. This caused change-capture config and other JSONB-backed fields to appear as `{}` and broke MinIO raw-topic routing.
- Harden MinIO raw adapter failure handling: failed normalization/routing is written to Kafka DLQ before committing the raw offset; if DLQ publishing fails, the offset is left uncommitted for retry.

## 0.9.0-beta.4.3.3
- Fixed PostgreSQL UUID decoding in deterministic catalogue recon-ID generation (`uuid.UUID` values returned by psycopg are now accepted directly).
- Included PostgreSQL catalogue search UUID cast hotfix.
- Retains beta.4.1 JSONB decoding and raw MinIO adapter DLQ/offset safety fixes.


## 0.9.0-beta.4.4.1
- Hardened catalogue lifecycle acceptance test preflight JSON validation.
- Replaced brittle jq catalogue-group discovery with validated Python parsing.
- Added explicit API response diagnostics when catalogue/change-capture endpoints return unexpected JSON shapes.
