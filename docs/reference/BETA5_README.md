# AMP Enterprise Beta — 0.9.0-beta.5.0

AMP is a protocol-neutral, metadata-aware object-data control plane. It lets applications interact with heterogeneous object stores through HCP-style REST and an S3-compatible API while AMP handles logical routing, rich metadata sidecars, administrative catalogue, migration, change capture and reconciliation.

## Product boundary

AMP is **not** an object store and is **not** a WORM/compliance engine.

Backend storage remains authoritative for native versions, WORM, retention, legal hold/Object Lock, lifecycle, pruning, durability, replication and encryption-at-rest. AMP observes, translates and reconciles backend state; it does not emulate regulatory semantics in PostgreSQL.

See `docs/BACKEND_AUTHORITY.md`.

## Managed object model

Every new AMP-managed logical object uses a stable `AMP_PACKAGE_V3` package ID and stable physical member keys across overwrites:

```text
<GUID>/
├── payload
├── annotations/
│   └── <annotation sidecars>
└── manifest.json
```

Placement is configurable per Catalogue Group:

```text
AMP_MANAGED_HASH:
.amp/objects/<2hex>/<2hex>/<GUID>/...

CLIENT_PATH:
<logical-client-key>/<GUID>/...
```

The package GUID is stable for `(Catalogue Group, logical object key)`. Rewriting the logical object rewrites the same physical payload key; if backend versioning is enabled the **backend** creates the next native VersionId.

AMP catalogues native payload/annotation versions and records payload-version -> annotation-version snapshot links for explicit historic reads. It does not own pruning or lifecycle.

## Client protocol adapters

### HCP REST beta subset

- object PUT / GET / HEAD / DELETE
- custom metadata PUT / GET / HEAD / DELETE
- explicit native version GET/HEAD

### S3 beta subset

- SigV4 header authentication (lab credentials)
- PutObject / GetObject / HeadObject / DeleteObject
- ListObjectsV2
- Range GET
- `x-amz-meta-*` and tags -> managed sidecars
- GetObjectTagging
- explicit backend version GET/HEAD
- HCP PUT -> S3 GET and S3 PUT -> HCP GET

Multipart, CopyObject, presigned URLs and the full S3 version-management API are deferred.

## Gateway response policy

Each Gateway Route can choose:

```text
RAW_BACKEND
AMP_NORMALIZED
AMP_NORMALIZED_WITH_BACKEND
```

and backend-header policy:

```text
NONE
SELECTED
ALL_SAFE
```

The backend always decides the operation outcome. AMP may change presentation, but it does not replace a backend 403/404/409/etc. with a different fundamental outcome. See `docs/GATEWAY_RESPONSE_POLICY.md`.

## Catalogue architecture

```text
Storage System + Namespace/Bucket = one logical Catalogue Group
```

The Catalogue is for administration/reconciliation, not end-user search. Each group has stable virtual shards, physical shard ranges, generations, tombstones, independent lifecycle and change-capture configuration.

HOP indexes directly from storage; it does not use Catalogue object rows. Search/AI artifacts carry the same deterministic logical recon ID so AMP can reconcile them later.

## Continuous external-data change capture

The lab includes Kafka:

```text
MinIO notification -> raw topic -> AMP adapter -> amp.storage.changes -> Catalogue worker
```

The architecture also contains adapters/scaffolding for AWS EventBridge/SQS and HCP MQE operation polling. Baseline discovery and periodic integrity audit remain separate safety mechanisms.

## Migration/hydration

Current beta migration copies the **current logical version** plus managed annotations into an independent target Catalogue Group using that target's package-placement policy. The target backend creates its own native versions. Historic-version migration and native compliance-state translation are future work.

Read-through hydration follows the same target-package semantics and registers the target only after the target payload write succeeds.

## Full lab

From WSL2/Linux:

```bash
./scripts/lab-preflight.sh
./scripts/lab-up.sh
./scripts/lab-smoke-test.sh
```

Open:

```text
AMP UI/API        http://localhost:8080
Primary MinIO     http://localhost:9000   console :9001
Legacy MinIO      http://localhost:9100   console :9101
Solr              http://localhost:8983
Tika              http://localhost:9998
Hop               http://localhost:8182
Kafka host        localhost:29092
```

Both MinIO buckets are configured with backend-native versioning in the lab so AMP's version-observation behavior can be tested.

## Clean reset for beta.5

Beta.5 changes logical version semantics and adds version/response audit tables. For this development lab, use a clean reset rather than reusing older beta volumes:

```bash
./scripts/lab-reset.sh
```

`lab-reset.sh` removes lab volumes and immediately rebuilds/bootstraps the lab.

## Test sequence

Follow `docs/BETA5_TEST_PLAN.md`. Key automated tests include:

```bash
./scripts/lab-event-smoke-test.sh
AMP_TEST_TOTAL=50 AMP_TEST_UPDATES=10 AMP_TEST_DELETES=5 AMP_TEST_TIMEOUT=900 ./scripts/lab-catalogue-lifecycle-test.sh
AMP_HCP_TEST_TOTAL=10 ./scripts/lab-hcp-rest-ingest-test.sh
./scripts/lab-package-placement-test.sh
AMP_S3_TEST_TOTAL=10 ./scripts/lab-s3-interop-test.sh
./scripts/lab-native-version-test.sh
./scripts/lab-response-policy-test.sh
./scripts/lab-migration-hydration-test.sh
```

Backend unit tests:

```bash
make test
```

Current packaged suite: **19 passing tests**.

## Beta boundaries

This is an enterprise-oriented beta, not a production-certified release. Production hardening still requires real HCP compatibility/MQE validation, AWS Inventory/S3 Metadata integration, real HOP/Solr pipelines, multipart S3, OIDC/RBAC (intentionally parked for this beta), HA, performance/failure testing, observability/SLOs and security review.
