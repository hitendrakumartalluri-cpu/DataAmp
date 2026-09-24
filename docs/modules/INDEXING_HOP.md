# Indexing and Apache Hop Module

## Responsibility

Builds connector-driven content processing from authoritative object storage into Solr metadata and full-text indexes.

## Locked architecture

```text
Native connector -> Apache Hop -> Tika/enrichment
                 -> metadata/tag normalization -> Solr indexes
```

Every projection records source identity, native version, content hash where available, pipeline version and index attempt state. AMP uses these fields to prove completeness and freshness.

## Current foundation

- Storage-direct Hop contract.
- Tika extraction and local pipeline simulator.
- Local search projection simulator.
- Storage and index reconciliation flows.
- Production Hop-to-Solr delivery is not implemented.

## Complete-product scope

Production Hop pipelines, durable indexing ledger/retry, dependency-aware selective reprocessing, workload lanes, metadata/full-text split, schema evolution, canonical conversion, dynamic collection wizard, index rollover/routing and large-estate reconciliation.

## Primary locations

- `services/hop/`
- `services/control-plane/app/services/processing.py`
- `services/control-plane/app/services/operations.py`

Tracking: [Index completeness #12](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/12), [split indexes #15](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/15), [collection wizard #17](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/17).
