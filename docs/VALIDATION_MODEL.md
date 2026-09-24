# Validation Model

AMP promotes a capability to **Verified** only after a dedicated acceptance test, cumulative regression and linked evidence.

## Functional cycles

| Cycle | What it proves |
|---|---|
| Connector readiness | Credentials, endpoint, capability discovery and least-privilege access are valid. |
| Inventory | A bounded or authoritative source inventory produces stable object identities. |
| Change capture | Creates, updates, metadata changes and deletes are normalized, deduplicated and ordered safely. |
| Indexing | Content, metadata and tags are projected with source version, hash and pipeline provenance. |
| Reconciliation | Missing, stale and orphaned index records are detected without silently repairing them. |
| Query routing | Only schema-compatible indexes receive each subquery and authorization filters are applied before execution. |
| Result consolidation | Field mapping, score normalization, deduplication and partial failures are explicit. |
| Analytics | Facet/stat totals reconcile to the indexed population and filters. |
| PII | Regex rules are validated, bounded, versioned and produce protected evidence without leaking values to logs. |
| Governance | Dry-run, approval, native action, idempotency and backend verification agree. |
| Performance/resilience | Throughput, latency, queue isolation, retry, recovery and HA satisfy release targets. |

## Architectural guarantees

- Native storage is authoritative for objects, versions, retention, locks and holds.
- AMP is not in the application object data path.
- The catalogue is administrative evidence; Solr is the search and analytics plane.
- Deletes cannot starve new-object indexing.
- A Solr match alone is insufficient authority for a compliance mutation.
