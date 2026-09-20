# Decision Matrix

Use this file only for unresolved choices. Accepted choices must move to an ADR.

| Topic | Options | Current leaning | Evidence needed | Target |
|---|---|---|---|---|
| Production catalogue partitioning | PostgreSQL schemas; databases; distributed SQL | Stable virtual shards with movable physical ranges | Scale/rebalance/failure tests | Beta 6 |
| HCP baseline feed | MQE; namespace listing; export | MQE where supported, listing fallback | Real HCP compatibility and completeness tests | Beta 6 |
| Large S3 baseline | Inventory; S3 Metadata tables; LIST | Inventory/events preferred at scale | Cost, freshness, completeness benchmarks | Beta 6 |
| Search split | Combined index; metadata/text split; tiered split | Workload-driven wizard | Representative query and export benchmarks | Future |
| Federated score merge | Raw score; normalized score; RRF | RRF | Relevance evaluation across heterogeneous schemas | Future |
| Production deployment | Kubernetes/Helm; OpenShift profile | Kubernetes-first | HA, security, upgrade, operations tests | GA candidate |

