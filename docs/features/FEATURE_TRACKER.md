# AMP Feature Tracker

Active umbrella issue: [AMP-CORE-EPIC-001 — #25](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/25)

## Active product scope

| ID | Capability | Target | State |
|---|---|---|---|
| AMP-CON-001 | Canonical connector capability contract | Next | Approved |
| AMP-CON-002 | AWS S3 inventory, events, metadata and governance adapter | Next | Partial scaffold |
| AMP-CON-003 | HCP MQE, object, metadata and governance adapter | Next | Partial scaffold |
| AMP-CON-004 | Azure Blob inventory, change feed, metadata and governance adapter | Later | Planned |
| AMP-CON-005 | VSP One Object connector | Later | Planned |
| AMP-IDX-001 | Full-text extraction through Hop and Tika | Next | Beta simulator |
| AMP-IDX-002 | Native metadata and tag normalization | Next | Planned |
| AMP-IDX-003 | Durable indexing ledger and retry state | Next | Approved design |
| AMP-IDX-004 | Dependency-aware selective reprocessing | Next | Approved design |
| AMP-REC-001 | Storage reconciliation | Beta | Verified in lab |
| AMP-REC-002 | Source-to-index completeness and freshness | Next | Approved design |
| AMP-SRCH-001 | Schema catalogue and field mappings | Beta | Implemented simulator |
| AMP-SRCH-002 | Intelligent heterogeneous query router | Beta | Implemented simulator |
| AMP-SRCH-003 | Result normalization, deduplication and consolidation | Beta | Implemented simulator |
| AMP-ANA-001 | Configurable facet/statistics dashboards | Beta | Implemented simulator |
| AMP-ANA-002 | Duplicate-object analytics | Beta | Implemented simulator |
| AMP-GOV-001 | Retention and hold policy dry-run | Beta | Implemented |
| AMP-GOV-002 | Native lock/hold apply and verification | Later | Approved design |
| AMP-GOV-003 | Field-scoped user regex PII scanning | Beta | Implemented |
| AMP-GOV-004 | Sensitive-object marking and evidence | Beta | Implemented |

## Retired scope

All `AMP-GW-*` client gateway and protocol-translation capabilities were retired on 2026-09-24. Their code and documentation are preserved on `archive/gateway-beta-2026-09-24`; they are not candidates for active implementation.
