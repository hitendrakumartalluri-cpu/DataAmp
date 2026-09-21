# AMP Product Capability Map

This page answers two questions:

1. What is present in AMP today?
2. What is intended to exist when the product reaches its complete enterprise scope?

The current baseline is **0.9.0-beta.5.0**. “Implemented” means runnable code exists; it does not mean production-certified. The [Feature Tracker](Feature-Tracker) and linked GitHub Issues remain the detailed delivery record.

## Product shape

AMP is designed as three cooperating planes:

| Plane | Purpose |
|---|---|
| Access and Migration | Protocol compatibility, routing, managed object packages, storage adapters, migration and hydration |
| Catalogue and Assurance | Administrative catalogues, change capture, lineage, reconciliation, audit and governance evidence |
| Search and Knowledge | Storage-direct indexing, extraction, Solr/AI retrieval, analytics and governed AI capabilities |

## Current Beta 5 capability

### Access and Migration

- Protocol-neutral managed-object service.
- HCP REST object and custom-metadata operations.
- S3-compatible Put/Get/Head/Delete/List subset, range reads and SigV4 lab authentication.
- Cross-protocol access to one logical managed object.
- AMP_PACKAGE_V3 with deterministic identity, manifests and portable annotation sidecars.
- AMP-managed hash or client-path package placement.
- Backend-native version observation and historic-version reads.
- Configurable raw or normalized Gateway responses and safe backend-header exposure.
- Current-object migration, dry-run planning and read-through hydration.
- Independent source and target catalogue identities connected by lineage.

### Catalogue and Assurance

- One Catalogue Group per storage system plus namespace/bucket/container.
- Stable container-local reconciliation IDs and 1024 virtual shards.
- Discovery generations, missing detection and tombstones.
- Kafka change-event backbone with MinIO event normalization.
- Idempotency, stale-event suppression, dead-letter and replay foundations.
- Storage, index and AI reconciliation beta flows.
- Native payload and annotation version inventory.
- Freeze, archive and decommission lifecycle.
- Administrative UI/API, audit history and scheduled reconciliation foundations.

### Search and Knowledge

- Storage-direct Apache Hop indexing contract, independent of the AMP catalogue.
- Tika extraction and local search/embedding simulator.
- Solr/index and AI-artifact reconciliation simulator.
- Dataset materialization and retrieval simulator.
- Management UI surfaces for catalogue, routes, placement, versions and schedules.

## Complete enterprise product scope

### Access and Migration

- Broader S3 compatibility including multipart upload, CopyObject, presigned URLs and expanded version APIs.
- Production HCP native adapter and MQE integration.
- Azure Blob and Google Cloud Storage translation adapters.
- Unified namespace and policy-based routing across multiple object stores.
- Enterprise identity through OIDC, LDAP/AD, RBAC, PKI and managed secrets.
- Scheduled migration policies with filters, priorities, concurrency, throttling, retries, checksums and reporting.
- Historic-version migration and backend-native compliance-state translation.
- WAN, scale, HA, failover, upgrade and disaster-recovery certification.

### Catalogue, Governance and Assurance

- Production AWS and HCP change adapters plus inventory-based large-estate baselines.
- Retention and legal-hold policy control plane that invokes and verifies native backend enforcement.
- Lifecycle-bound search and retrieval audit history.
- Compliance evidence packs and chain-of-custody reporting.
- PII scan policies and governed results.
- Duplicate and encrypted-document detection.
- Policy-based storage optimisation.
- Authorization-map enforcement across search, facets, previews, exports and downloads.
- Production observability, SLOs, alerts, capacity guidance and security certification.

### Search, Analytics and AI

- Production Apache Hop pipelines and Solr writer at enterprise scale.
- Metadata/full-text index separation with consistency and query-routing controls.
- Dynamic Solr collection design and auto-scaling wizard.
- Heterogeneous federated search through schema catalogue, index map, routing and rank fusion.
- Indexing completeness ledger and durable retry/error handling.
- Schema evolution and canonical data conversion.
- Governed asynchronous metadata export and document download.
- AI-assisted natural-language search with field discovery and query-cost guardrails.
- AI-powered usage, billing, ageing and document-type dashboards.
- Document classification and auto-tagging.
- AI access guardrails and governed dataset lifecycle.

## Boundaries that remain true at completion

- Storage backends—not AMP—remain authoritative for durability, native versions, WORM, retention, legal hold, lifecycle, replication and deletion acceptance.
- AMP may request, observe, compare and report native compliance state; it must not emulate unsupported enforcement in PostgreSQL.
- The AMP catalogue remains an administrative, audit and reconciliation plane—not the end-user search engine.
- Apache Hop reads storage directly and writes Solr/AI indexes using the shared deterministic identity contract.
- A migrated or hydrated target is registered only after its physical write succeeds.
- Every production claim requires linked acceptance and non-functional evidence.
