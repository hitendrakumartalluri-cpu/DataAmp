# ADR-0004: Separate Administrative Catalogue and Search Planes

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

The administrative catalogue and end-user content search have different schemas, workloads, scaling, and lifecycles.

## Decision

Apache Hop reads object storage directly, performs extraction/enrichment, and writes Solr/AI indexes. It does not read `catalogue_objects`. AMP and Hop share deterministic identity rules; AMP reconciles downstream indexes asynchronously.

## Consequences

Catalogue outages do not become the indexing data path, and PostgreSQL is not used as enterprise search. Identity contracts and reconciliation become critical integration interfaces.

