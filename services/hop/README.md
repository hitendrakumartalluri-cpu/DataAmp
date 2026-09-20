# Apache HOP Integration Contract

Target: Apache Hop 2.19.

HOP is a **storage-to-index/data-manipulation executor**. It does **not** read or write AMP Catalogue object rows during normal indexing.

## Production indexing flow

1. HOP is configured with a storage system and one namespace/bucket.
2. HOP enumerates/consumes objects directly from that source.
3. HOP derives the AMP deterministic `recon_id` using the exported catalogue/source identity contract.
4. HOP fetches/streams the payload from storage.
5. Tika/specialised parsers extract content and metadata.
6. HOP applies enrichment, chunking and AI transforms.
7. HOP writes Solr/vector documents containing at minimum:
   - `_amp_source_id`
   - `_amp_container_type`
   - `_amp_container_name`
   - `_amp_recon_id`
   - `_amp_source_version`
   - `_amp_content_hash`
   - `_amp_pipeline_version`
   - `_amp_indexed_at`
8. AMP later reconciles these fields against the independent administrative catalogue.

## Catalogue independence

HOP must **not** query `catalogue_objects` to decide what to index. This avoids making PostgreSQL a runtime dependency of the search ingestion path and allows catalogue shards to be rebuilt, frozen or decommissioned independently.

For data manipulation, HOP may emit a lightweight `OBJECT_CHANGED` hint to AMP after modifying storage. The hint schedules a targeted refresh; it never directly mutates catalogue rows.

## Deterministic reconciliation identity

Each catalogue group has a persistent `recon_namespace`. The object recon ID is UUIDv5 over:

```text
recon_namespace + object_key + native_version_id
```

The same contract is implemented in the beta HOP simulator (`ProcessingService.index_source`) without reading `catalogue_objects`.
