# Product Vision

DataAmp is an enterprise Archive Modernization Platform that lets organizations administer, access, migrate, reconcile, search, and prepare object data for AI across heterogeneous storage systems without making AMP the storage system of record.

## Product planes

1. **Access and migration:** protocol compatibility, storage adapters, routing, package/annotation management, batch migration, and read-through hydration.
2. **Catalogue and assurance:** container-scoped administrative catalogues, change capture, generations, reconciliation, audit, lineage, and decommission evidence.
3. **Search and knowledge:** storage-direct Hop pipelines, extraction/enrichment, Solr and AI indexes, federated retrieval, datasets, and governed downstream validation.

## Principles

- Storage backends remain authoritative for durability, native versions, replication, WORM, retention, legal hold, lifecycle, encryption, and deletion decisions.
- One storage system plus namespace/bucket/container defines one logical Catalogue Group.
- The catalogue is an administrative truth and evidence plane, not an end-user search engine.
- Search and AI pipelines read storage directly and share deterministic object identity with AMP.
- Migration never registers a target object until its physical target write succeeds.
- Protocol-specific behavior is isolated behind adapters; common logic operates on canonical managed objects.
- Capabilities are promoted only with explicit acceptance evidence.

## Non-goals

- Replacing backend compliance enforcement with database flags.
- Owning backend replication or version pruning.
- Using PostgreSQL catalogue queries as enterprise content search.
- Claiming full S3 or HCP compatibility before the compatibility matrix proves it.

