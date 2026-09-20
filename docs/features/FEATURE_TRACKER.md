# AMP Feature Tracker

**Baseline:** 0.9.0-beta.5.0  
**Status rule:** Implemented means code exists; Verified requires recorded passing evidence.

## Gateway and packages

| ID | Capability | Release | Status | Acceptance/evidence |
|---|---|---|---|---|
| AMP-GW-001 | Protocol-neutral managed-object service | Beta 5 | Implemented | Unit tests; clean-lab rerun pending |
| AMP-GW-002 | HCP REST object PUT/GET/HEAD/DELETE | Beta 5 | Implemented | `lab-hcp-rest-ingest-test.sh` |
| AMP-GW-003 | HCP custom-metadata CRUD | Beta 5 | Implemented | HCP ingest test validates sidecars |
| AMP-GW-004 | S3 Put/Get/Head/Delete/ListObjectsV2 | Beta 5 | Implemented | `lab-s3-interop-test.sh` |
| AMP-GW-005 | SigV4 header authentication with lab credentials | Beta 5 | Implemented | S3 interop test; production identity deferred |
| AMP-GW-006 | Range GET and user metadata/tags | Beta 5 | Implemented | S3 interop test |
| AMP-GW-007 | HCP/S3 cross-protocol access | Beta 5 | Implemented | S3 interop test |
| AMP-GW-008 | Per-route raw/normalized response policy | Beta 5 | Implemented | `lab-response-policy-test.sh` |
| AMP-GW-009 | Safe backend-header exposure and bounded response capture | Beta 5 | Implemented | Response policy test |
| AMP-GW-010 | Native VersionId inventory and explicit old-version GET | Beta 5 | Implemented | `lab-native-version-test.sh` |
| AMP-GW-011 | Payload-version to annotation-snapshot mapping | Beta 5 | Implemented | Native version test |
| AMP-GW-012 | Multipart S3 | Beta 6 | Deferred | Multipart compatibility suite required |
| AMP-GW-013 | CopyObject and presigned URLs | Beta 6 | Deferred | SDK compatibility suite required |
| AMP-GW-014 | OIDC/LDAP/AD and RBAC | Beta 6 | Deferred | Identity, role, route, and negative tests required |
| AMP-PKG-001 | AMP_PACKAGE_V3 stable GUID package | Beta 5 | Implemented | Package placement and ingest tests |
| AMP-PKG-002 | AMP-managed hash placement | Beta 5 | Implemented | `lab-package-placement-test.sh` |
| AMP-PKG-003 | Client-path placement | Beta 5 | Implemented | Package placement test |

## Catalogue, change capture, and scheduling

| ID | Capability | Release | Status | Acceptance/evidence |
|---|---|---|---|---|
| AMP-CAT-001 | One Catalogue Group per storage system + container | Beta 5 | Implemented | Lifecycle and schema tests |
| AMP-CAT-002 | Stable container-local recon IDs | Beta 5 | Implemented | Lifecycle test verifies stability |
| AMP-CAT-003 | 1024 virtual shards mapped to physical ranges | Beta 5 | Implemented | Catalogue lifecycle test |
| AMP-CAT-004 | Generations, MISSING and TOMBSTONED lifecycle | Beta 5 | Implemented | `lab-catalogue-lifecycle-test.sh` |
| AMP-CAT-005 | Native payload/annotation version inventory | Beta 5 | Implemented | Native version test |
| AMP-CAT-006 | Freeze/archive/decommission lifecycle | Beta 5 | Implemented | API/unit coverage; lab rerun pending |
| AMP-EVT-001 | Kafka normalized change-event backbone | Beta 5 | Implemented | Event smoke test |
| AMP-EVT-002 | MinIO notifications to raw and normalized topics | Beta 5 | Implemented | `lab-event-smoke-test.sh` |
| AMP-EVT-003 | Idempotency, stale suppression, DLQ, replay | Beta 5 | Implemented | Unit/event tests; failure-injection expansion needed |
| AMP-EVT-004 | AWS SQS/EventBridge change adapter | Beta 6 | In Progress | Scaffold only; real integration pending |
| AMP-EVT-005 | HCP MQE change adapter | Beta 6 | In Progress | Scaffold only; real integration pending |
| AMP-OPS-001 | Reconciliation schedule management | Beta 5 | Implemented | Scheduler API/unit coverage |

## Discovery, migration, and reconciliation

| ID | Capability | Release | Status | Acceptance/evidence |
|---|---|---|---|---|
| AMP-MIG-001 | Generic LIST discovery baseline | Beta 5 | Implemented | Lifecycle test |
| AMP-MIG-002 | Manifest-based package reconstruction | Beta 5 | Implemented | Package and lifecycle tests |
| AMP-MIG-003 | Current logical object and annotation migration | Beta 5 | Implemented | `lab-migration-hydration-test.sh` |
| AMP-MIG-004 | Independent source/target catalogues and lineage | Beta 5 | Implemented | Migration/hydration test |
| AMP-MIG-005 | Dry-run migration planning | Beta 5 | Implemented | API/unit coverage |
| AMP-MIG-006 | Primary-first read-through hydration | Beta 5 | Implemented | Migration/hydration test |
| AMP-MIG-007 | Scheduled migration filters, priority, concurrency, throttle, retry, reporting | Beta 6 | Approved | Detailed implementation and acceptance tests pending |
| AMP-MIG-008 | Historic-version migration and mapping | GA candidate | Deferred | Version-by-version evidence required |
| AMP-MIG-009 | Native compliance-state translation/reconciliation | GA candidate | Deferred | Only backend-native enforcement permitted |
| AMP-REC-001 | Storage reconciliation | Beta 5 | Implemented | Lab/API validation pending rerun |
| AMP-REC-002 | Solr/index reconciliation and orphan detection | Beta 5 | Implemented | Simulator beta; production pipeline evidence pending |
| AMP-REC-003 | AI artifact reconciliation | Beta 5 | Implemented | Simulator beta; production evidence pending |

## Indexing, search, analytics, governance, and AI

| ID | Capability | Release | Status | Acceptance/evidence |
|---|---|---|---|---|
| AMP-IDX-001 | Storage-direct HOP indexing contract | Beta 5 | Implemented | Contract and lab simulator only |
| AMP-IDX-002 | Tika extraction and local search/embedding simulator | Beta 5 | Implemented | Lab validation pending rerun |
| AMP-IDX-003 | Production HOP-to-Solr pipeline | Beta 6 | Deferred | Scale, retry, DLQ, schema, and reconciliation tests |
| AMP-IDX-004 | Heterogeneous federated search | Future | Approved | Schema catalogue, field mapping, routing, normalization, RRF |
| AMP-IDX-005 | Separate metadata and heavy full-text indexes | Future | Proposed | Benchmark routing, consistency, joins, and failure modes |
| AMP-IDX-006 | Dynamic collection design wizard | Future | Proposed | Inputs include volume, size, query patterns, hot/cold fields |
| AMP-IDX-007 | Date normalization and client-format conversion | Future | Proposed | Canonical storage format plus strict parser/profile rules |
| AMP-GOV-001 | Configurable pre-search authorization map | Future | Approved | Deny precedence, group resolution, filter injection, audit |
| AMP-GOV-002 | PII token policies, field mapping, scans, and reports | Future | Proposed | Regex safety, sampling, false-positive controls, secure results |
| AMP-GOV-003 | Governed asynchronous metadata/document export | Future | Approved | Export handler, throttle, encryption, owner-only links, expiry |
| AMP-AI-001 | Dataset materialization and retrieval simulator | Beta 5 | Implemented | Lab simulator; production controls pending |
| AMP-AI-002 | AI-recommended billing/age/doc-type dashboards | Future | Proposed | Use source object size, not Solr document size |
| AMP-AI-003 | Natural-language query assistant | Future | Proposed | Field discovery, query preview, cost limits, approval controls |

## UI, deployment, and operability

| ID | Capability | Release | Status | Acceptance/evidence |
|---|---|---|---|---|
| AMP-UI-001 | Catalogue, shard, generation, and object views | Beta 5 | Implemented | UI/API lab validation |
| AMP-UI-002 | Package placement, routes, schedules, and response policy | Beta 5 | Implemented | UI/API lab validation |
| AMP-UI-003 | Light, attractive guided administration experience | Beta 6 | Approved | Accessibility and usability acceptance needed |
| AMP-OPS-002 | Docker Compose full lab | Beta 5 | Implemented | Clean-lab acceptance plan |
| AMP-OPS-003 | kind/Kubernetes lab starter | Beta 5 | Implemented | Starter only |
| AMP-OPS-004 | Helm product deployment starter | Beta 5 | Implemented | Not HA/production-certified |
| AMP-OPS-005 | Health/readiness, audit, event, and job history | Beta 5 | Implemented | Unit/lab coverage |
| AMP-OPS-006 | Prometheus, OpenTelemetry, dashboards, alerts, SLOs | Beta 6 | Deferred | Production telemetry acceptance required |
| AMP-OPS-007 | HA, failover, backup/restore, upgrade, WAN and scale | GA candidate | Deferred | Formal non-functional test programme required |

