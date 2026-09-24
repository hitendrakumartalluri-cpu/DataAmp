# AMP Roadmap

## Phase 1 — Connector and index assurance foundation

- Canonical connector contract for inventory, object reads, metadata/tags, events and governance state.
- AWS S3 and HCP connectors.
- Apache Hop/Tika full-text and metadata indexing into Solr.
- Durable indexing ledger, selective reprocessing and source-to-index reconciliation.
- Workload separation for creates, updates, deletes and repair.

## Phase 2 — Search and analytics

- Metadata/full-text index strategy and schema catalogue.
- Intelligent query router using user mappings and index capabilities.
- Heterogeneous result normalization, deduplication and consolidation.
- Configurable facet/stat dashboards: age, size, type, retention, sensitivity and duplicates.
- Async metadata/object export.

## Phase 3 — Governance

- Retention policy compiler, dry-run, approval and explainability.
- Native Object Lock/retention/hold adapters with verification.
- User-managed regex PII rules with field targeting and sensitivity evidence.
- Coverage, exception, ageing, hold and compliance reports.

## Phase 4 — Platform expansion

- Azure Blob Storage connector.
- VSP One Object connector.
- Cross-platform federated search and governance reporting.
- Scale, HA, security and production certification.

## Explicit non-goals

- Client-facing storage gateway or protocol translation.
- Replacing object-storage durability, replication or native APIs.
- Emulating WORM/Object Lock using database flags.
