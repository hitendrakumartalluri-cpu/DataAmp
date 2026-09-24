# DataAmp — Archive Modernization Platform

AMP is an enterprise indexing, search, analytics and governance-assurance platform for existing object storage.

AMP connects to AWS S3, Azure Blob Storage, Hitachi Content Platform (HCP) and VSP One Object. It inventories objects, extracts full text, indexes native metadata and tags, provides federated search across heterogeneous Solr indexes, and drives explainable retention and hold workflows through native storage APIs.

AMP is **not** a storage gateway and does not replace the client-facing S3, Azure or HCP APIs.

## Active product planes

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

Open `http://localhost:8080` for the enterprise console or `http://localhost:8080/docs` for the API. Demo mode seeds two local storage scopes, a processing pipeline, a Solr-compatible index, a PII rule and a dry-run hold policy.

## Repository boundaries

| Area | Purpose |
|---|---|
| `services/` | Active connector, catalogue, indexing, search and assurance implementation |
| `docs/` | Canonical product and architecture documentation |
| `lab/` | Simulator, integration and acceptance environment |
| `archive/` | Superseded non-runtime material |

The complete gateway implementation and its tests are preserved on branch [`archive/gateway-beta-2026-09-24`](https://github.com/hitendrakumartalluri-cpu/DataAmp/tree/archive/gateway-beta-2026-09-24).
