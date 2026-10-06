# HCP-focused beta 0.11.0-beta.2

The immediate demonstration scope is HCP collection, extraction, metadata transformation, indexing and search. Start with the [HCP demo runbook](docs/releases/BETA_0_11_0_HCP_DEMO.md). Native REST and the synthetic source are explicitly distinguished; non-HCP expansion and incomplete enterprise acceptance remain deferred.

# Project Status

## Current direction

The gateway product line was retired on 2026-09-24. Active development now includes managed archive ingestion alongside connector-based indexing, search, analytics, reconciliation and governance. ADR-0012 adds bounded archive writes without restoring gateway protocols.

## Preserved baseline

The former Gateway Beta implementation, documentation and regression evidence are preserved on `archive/gateway-beta-2026-09-24`. It remains historical evidence and is not part of the active product runtime.

## Active foundations

- Storage-system and container-scoped catalogue model.
- Storage discovery and normalized change events.
- Tika/local extraction simulator and search projection.
- Deterministic object identity and reconciliation findings.
- HCP MQE and AWS/MinIO event-adapter scaffolding.

## 0.11.0-beta.1

A runnable archive/workbench beta now includes durable ingestion, typed mapping, local verified payload and metadata receipts, independent index repair, access-filtered search, export/evidence jobs and a console. All 27 issues are mapped to implemented slices, planning tools or deferred/native qualification gates in [release notes](docs/releases/BETA_0_11_0.md). Local regression: 28 passing tests plus Python/JS/shell checks. Real endpoints, external Solr and PostgreSQL qualification remain separate gates.

## Next delivery gate

Define the canonical connector contract and prove AWS S3 plus HCP read-only ingestion through full-text/metadata indexing, search and source-to-index reconciliation. Azure Blob and VSP One Object follow through the same contract.

No current Beta line is production-certified.
