# HCP-focused beta 0.11.0-beta.2

The immediate demonstration scope is HCP collection, extraction, metadata transformation, indexing and search. Start with the [HCP demo runbook](docs/releases/BETA_0_11_0_HCP_DEMO.md). Native REST and the synthetic source are explicitly distinguished; non-HCP expansion and incomplete enterprise acceptance remain deferred.

# DataAmp — Archive Modernization Platform

AMP is an archiving, indexing, search, analytics and governance-assurance platform. The 0.11 beta adds managed document ingestion, metadata mapping and verified archive receipts alongside existing storage discovery.

AMP has connector adapters for AWS S3, Azure Blob Storage, HCP and VSP One Object. The runnable beta uses local search projections; external connector, Solr, Hop and native governance qualification is explicitly tracked in the release notes.

AMP is **not** a storage gateway and does not replace the client-facing S3, Azure or HCP APIs.

## Beta 0.11

- [Release notes and all 27 issue coverage](docs/releases/BETA_0_11_0.md)
- [Archive ingestion decision](docs/adr/ADR-0012-managed-archive-ingestion.md)
- [Example ingestion batch](examples/archive/)

```bash
./scripts/run-beta.sh
```

Open http://localhost:8080. The local script uses SQLite and persists documents and receipts under `data/`. Demo mode is for local evaluation. See the release notes for identity configuration and production qualification limits.

## Active product planes

- Managed archive ingestion, mapping, payload/metadata verification and independent index state.
- Storage connectors and continuous change capture.
- Full-text, metadata and tag indexing through Apache Hop, Tika and Solr.
- Source-to-index reconciliation and repair evidence.
- Intelligent query routing and heterogeneous result consolidation.
- Configurable analytics using Solr facets and statistics.
- User-defined regex PII scanning and sensitivity marking.
- Rule-based retention, Object Lock and legal-hold orchestration with native verification.

## Start here

- [Current status](PROJECT_STATUS.md)
- [Roadmap](ROADMAP.md)
- [Product vision](docs/PRODUCT_VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Feature tracker](docs/features/FEATURE_TRACKER.md)
- [Gateway retirement archive](docs/archive/GATEWAY_BETA_ARCHIVE.md)
- [Product Wiki](https://github.com/hitendrakumartalluri-cpu/DataAmp/wiki)
- [0.10.0 Beta 1 release notes](docs/releases/BETA_0_10_0.md)

## Run the Beta

```bash
docker compose up --build
```

Open `http://localhost:8080` for the enterprise console or `http://localhost:8080/docs` for the API. Demo mode also seeds an archive profile and invoice. Compose uses PostgreSQL; the local script is the locally tested SQLite path.

## Repository boundaries

| Area | Purpose |
|---|---|
| `services/` | Active connector, catalogue, indexing, search and assurance implementation |
| `docs/` | Canonical product and architecture documentation |
| `lab/` | Simulator, integration and acceptance environment |
| `archive/` | Superseded non-runtime material |

The complete gateway implementation and its tests are preserved on branch [`archive/gateway-beta-2026-09-24`](https://github.com/hitendrakumartalluri-cpu/DataAmp/tree/archive/gateway-beta-2026-09-24).
