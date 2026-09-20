# DataAmp — Archive Modernization Platform

DataAmp (AMP) is an enterprise object-data control plane for protocol compatibility, catalogue assurance, migration, reconciliation, search preparation, and AI readiness.

The current implementation baseline is **0.9.0-beta.5.0**. It is suitable for controlled lab validation and is **not production-certified**.

## Start here

- [Current status](PROJECT_STATUS.md)
- [Roadmap](ROADMAP.md)
- [Product vision](docs/PRODUCT_VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Feature tracker](docs/features/FEATURE_TRACKER.md)
- [Architecture decisions](docs/adr/README.md)
- [Lab environment](lab/README.md)
- [Contribution and governance](CONTRIBUTING.md)
- [Browsable Wiki](https://github.com/hitendrakumartalluri-cpu/DataAmp/wiki) — generated from repository documentation

## Repository boundaries

| Area | Purpose |
|---|---|
| `services/` | Product implementation |
| `deployments/` | Product deployment packaging |
| `docs/` | Canonical product, feature, architecture, API, and decision documentation |
| `lab/` | Lab-only topology, scripts, manifests, credentials, and acceptance instructions |
| `archive/` | Superseded material retained for history |

GitHub is the canonical source. Chat discussions are working material until captured here in a feature, ADR, status update, test, or code change.

The Wiki is automatically generated from this repository. Direct Wiki edits are overwritten by the next documentation sync.

## Core architectural boundary

The AMP Catalogue is for administration, evidence, migration, and reconciliation. It is not the end-user search index. Production indexing reads storage directly through Apache Hop and writes to Solr/AI systems using AMP's deterministic identity contract.
