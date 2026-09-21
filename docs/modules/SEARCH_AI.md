# Search, Analytics and AI Module

## Responsibility

Provides end-user retrieval and knowledge capabilities over Solr/AI indexes while preserving authorization, explainability and reconciliation.

## Current Beta 5

- Solr is defined as the end-user search plane.
- Local search/embedding simulator.
- Dataset materialization and retrieval simulator.
- Index and AI artifact reconciliation.
- Management surfaces are beta; production search pipelines are pending.

## Complete-product scope

- Heterogeneous federated search using schema catalogue, index map, query routing and rank fusion.
- Field-aware natural-language query assistance with cost/safety controls.
- Metadata and document export jobs.
- Billing, ageing, document-type and usage dashboards.
- Document classification and auto-tagging.
- Governed datasets, model/pipeline provenance and AI access guardrails.

## Boundaries

The catalogue does not serve user search. Search authorization must be applied before query execution, facets, previews, exports and downloads.

Tracking: [natural-language search #19](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/19), [analytics #20](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/20).
