# Project Status

## Current direction

The gateway product line was retired on 2026-09-24. Active development now targets connector-based indexing, search, analytics, reconciliation and governance for AWS S3, Azure Blob Storage, HCP and VSP One Object.

## Preserved baseline

The former Gateway Beta implementation, documentation and regression evidence are preserved on `archive/gateway-beta-2026-09-24`. It remains historical evidence and is not part of the active product runtime.

## Active foundations

- Storage-system and container-scoped catalogue model.
- Storage discovery and normalized change events.
- Tika/local extraction simulator and search projection.
- Deterministic object identity and reconciliation findings.
- HCP MQE and AWS/MinIO event-adapter scaffolding.

## Next delivery gate

Define the canonical connector contract and prove AWS S3 plus HCP read-only ingestion through full-text/metadata indexing, search and source-to-index reconciliation. Azure Blob and VSP One Object follow through the same contract.

No current Beta line is production-certified.
