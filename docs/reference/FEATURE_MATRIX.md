# AMP 0.9.0-beta.5.0 Feature Matrix

Legend: **Implemented** = runnable in this package. **Beta** = runnable but not production-certified. **Scaffold/Next** = interface/direction only or subsequent increment.

| Domain | Capability | Status |
|---|---|---|
| Gateway | Canonical protocol-neutral managed object service | **Implemented beta** |
| Gateway | HCP REST PUT/GET/HEAD/DELETE | **Implemented beta** |
| Gateway | HCP custom-metadata PUT/GET/HEAD/DELETE | **Implemented beta** |
| Gateway | S3 Put/Get/Head/Delete/ListObjectsV2 | **Implemented beta** |
| Gateway | AWS SigV4 header authentication (lab credentials) | **Implemented beta** |
| Gateway | S3 Range GET | **Implemented beta** |
| Gateway | S3 user metadata/tags as managed sidecars | **Implemented beta** |
| Gateway | HCP<->S3 cross-protocol reads | **Implemented beta** |
| Gateway | Per-route RAW/normalized response modes | **Implemented beta** |
| Gateway | Backend header policy NONE/SELECTED/ALL_SAFE | **Implemented beta** |
| Gateway | Bounded raw backend response/audit capture | **Implemented beta** |
| Gateway | Native backend VersionId observation + explicit old-version GET | **Implemented beta** |
| Gateway | Payload-version -> annotation-version snapshot mapping | **Implemented beta** |
| Gateway | Backend compliance state visibility (where backend exposes it) | **Implemented beta** |
| Gateway | AMP-managed WORM/retention/legal-hold/lifecycle | **Intentionally NOT implemented** |
| Gateway | Multipart S3 | Next |
| Gateway | CopyObject / presigned URLs / full S3 version listing APIs | Next |
| Gateway | OIDC/LDAP/RBAC | **Parked for this beta** |
| Packages | `AMP_PACKAGE_V3` stable GUID package | **Implemented** |
| Packages | `AMP_MANAGED_HASH` placement `.amp/objects/aa/bb/<GUID>/` | **Implemented** |
| Packages | `CLIENT_PATH` placement `<client-key>/<GUID>/` | **Implemented** |
| Packages | Common interior `payload`, `annotations/`, `manifest.json` | **Implemented** |
| Catalogue | One logical Catalogue Group per storage system + bucket/namespace | **Implemented** |
| Catalogue | Stable logical recon ID independent of backend native version | **Implemented** |
| Catalogue | 1024 virtual shards + physical shard ranges | **Implemented** |
| Catalogue | Discovery generations + MISSING/TOMBSTONED lifecycle | **Implemented** |
| Catalogue | Native payload/annotation version inventory | **Implemented beta** |
| Catalogue | Freeze/archive/decommission lifecycle | **Implemented** |
| Change capture | Kafka event backbone | **Implemented beta** |
| Change capture | MinIO notifications -> raw topic -> normalized event | **Implemented** |
| Change capture | Idempotency/stale event suppression/DLQ/replay | **Implemented beta** |
| Change capture | AWS SQS/EventBridge adapter | Scaffold/Beta |
| Change capture | HCP MQE operations adapter | Scaffold/Beta |
| Discovery | Generic LIST baseline | **Implemented beta** |
| Discovery | Package reconstruction from manifest | **Implemented beta** |
| Discovery | AWS Inventory / S3 Metadata tables | Next |
| Discovery | Real HCP MQE object baseline | Next |
| Scheduling | Baseline/integrity/storage/index/AI recon schedules | **Implemented beta** |
| Migration | Current logical object migration + annotation copy | **Implemented beta** |
| Migration | Independent source/target catalogue + lineage | **Implemented** |
| Migration | Dry run | **Implemented** |
| Migration | Read-through hydration to target package | **Implemented beta** |
| Migration | Historic-version migration/mapping | Next |
| Migration | Native compliance-state translation/reconciliation | Next (backend-native only) |
| Reconciliation | Storage recon | **Implemented** |
| Reconciliation | Index recon + orphan detection | **Implemented beta** |
| Reconciliation | AI recon | **Implemented beta** |
| HOP/Search | HOP storage scan does not depend on Catalogue | **Implemented contract + lab simulator** |
| HOP/Search | Tika extraction + local search/embedding simulator | **Implemented beta** |
| HOP/Search | Production HOP pipelines + Solr writer | Next |
| Datasets/AI | Dataset materialization and retrieval simulator | **Implemented beta** |
| UI | Catalogue groups/shards/generations/object detail | **Implemented** |
| UI | Package placement configuration | **Implemented** |
| UI | Change capture + schedules | **Implemented** |
| UI | Gateway routes + response policy | **Implemented beta** |
| UI | Backend-native versions / observed compliance state | **Implemented beta** |
| Deployment | Docker Compose full lab | **Implemented** |
| Deployment | kind/Kubernetes starter | **Implemented starter** |
| Deployment | PostgreSQL + MinIO + Kafka + Solr + Tika + Hop | **Implemented lab** |
| Observability | health/readiness, event/audit/job history | **Implemented beta** |
| Observability | Prometheus/OpenTelemetry/SLO dashboards | Next |
