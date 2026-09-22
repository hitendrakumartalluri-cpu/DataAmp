# Roadmap

Roadmap ordering describes intent, not a delivery commitment. GitHub Issues hold live status.

## Completed baseline — Beta 5 functional freeze

- Frozen functional baseline: `0.9.0-beta.5.0.6`.
- Clean-lab full cumulative regression passed on 2026-09-22.
- Gateway response modes, native versions, package placement, HCP/S3 interoperability, migration, hydration, change capture, and Catalogue lifecycle are accepted in the lab topology.
- Reconciliation depth and non-functional certification remain separate gates.

## Next — Beta 6 hardening

- Real HCP native adapter and MQE baseline/delta integration.
- Production Apache Hop pipelines and Solr writer.
- OIDC, LDAP/AD integration, RBAC, route authorization, and managed secrets.
- Multipart S3, CopyObject, presigned URLs, and expanded compatibility testing.
- Prometheus/OpenTelemetry instrumentation, dashboards, alerts, and SLOs.
- HA/failover, upgrade, backup/restore, WAN, scale, and performance testing.

## Later — GA candidate

- Historic-version migration and source/target version mapping.
- Backend-native retention, legal-hold, WORM, and lifecycle state translation/reconciliation.
- S3 Inventory/S3 Metadata ingestion and large-estate discovery optimization.
- Production deployment profiles, capacity guidance, security assessment, and operational runbooks.

## Future product tracks

- Heterogeneous federated search using a schema catalogue, index map, query router, and rank aggregation.
- Metadata/full-text index separation with application-level query routing.
- Governed asynchronous metadata and document export.
- Configurable pre-search authorization maps.
- PII scan policies and reporting over indexed fields.
- Source-object-size billing, age, and document-type analytics with AI-recommended dashboards.
- Natural-language query assistance with field discovery and cost guardrails.

