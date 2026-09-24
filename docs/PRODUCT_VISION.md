# Product Vision

AMP makes enterprise object stores searchable, measurable and governable without moving ownership of the objects away from their native platform.

## Target platforms

- AWS S3
- Azure Blob Storage
- Hitachi Content Platform, including its native metadata model
- VSP One Object

## Product planes

1. **Connect and observe:** inventory, metadata/tags, events, versions and governance state.
2. **Index and assure:** full-text extraction, selective processing, Solr projection and reconciliation.
3. **Search and analyse:** schema-aware routing, heterogeneous result consolidation, facets and statistics.
4. **Govern:** PII discovery, retention and hold policies, native backend actions, verification and evidence.

## Principles

- Storage remains authoritative for object bytes, versions, retention, Object Lock and legal holds.
- Connectors are capability-driven; unsupported features are reported rather than emulated.
- Every indexed projection is traceable to a source object version and pipeline version.
- Policy candidates may be selected from Solr, but destructive or compliance actions require source verification.
- Query authorization is applied before search, facets, exports and retrieval.
- A delete storm must not starve new-object indexing.

## Non-goals

- A storage gateway or universal object API.
- Replacing AWS, Azure, HCP or VSP One Object.
- Full cloud-governance-suite parity.
- Claiming regulatory compliance solely because a policy exists in AMP.
