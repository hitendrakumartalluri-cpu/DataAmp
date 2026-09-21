# Indexing and Apache Hop Module

## Responsibility

Builds the storage-direct content processing path from authoritative object storage into Solr and AI indexes.

## Locked architecture

```text
Object storage -> Apache Hop -> Tika/enrichment
               -> transformation/chunking -> Solr/AI indexes
```

Hop does not read `catalogue_objects`. Hop and AMP independently derive the same reconciliation identity; AMP later compares catalogue truth with downstream index state.

## Current Beta 5

- Storage-direct Hop contract.
- Package-manifest reconstruction contract.
- Tika extraction and local pipeline simulator.
- Local search/embedding simulator.
- Index and AI reconciliation beta flows.
- Production Hop-to-Solr delivery is not implemented.

## Complete-product scope

Production Hop pipelines, 5,000 documents/second evidence, durable indexing ledger/retry, metadata/full-text split, schema evolution, canonical conversion, dynamic collection wizard, index rollover/routing and large-estate reconciliation.

## Primary locations

- `services/hop/`
- `services/control-plane/app/services/processing.py`
- `services/control-plane/app/services/operations.py`

Tracking: [Index completeness #12](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/12), [split indexes #15](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/15), [collection wizard #17](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/17).
