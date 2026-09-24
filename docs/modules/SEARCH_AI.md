# Search and Analytics Module

## Responsibility

Provides end-user retrieval and analytics over heterogeneous Solr indexes while preserving authorization, explainability and reconciliation.

## Current foundation

- Solr is defined as the end-user search plane.
- Local search simulator.
- Dataset materialization and index reconciliation.
- Management surfaces are beta; production search pipelines are pending.

## Complete-product scope

- Heterogeneous federated search using schema catalogue, user field mappings, index map, query routing and result consolidation.
- Field-aware natural-language query assistance with cost/safety controls.
- Metadata and document export jobs.
- Configurable Solr facet/stat dashboards for duplicates, age, size, type, retention and sensitivity.
- Duplicate detection using strong hashes with explicitly labelled heuristic fallback.

## Boundaries

The catalogue does not serve user search. Search authorization must be applied before query execution, facets, previews, exports and downloads.

Tracking: [natural-language search #19](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/19), [analytics #20](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/20).
