# ADR-0007: Configurable Gateway Response Presentation

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Some clients require backend-native behavior while others require one vendor-neutral contract.

## Decision

Configure each gateway route with `RAW_BACKEND`, `AMP_NORMALIZED`, or `AMP_NORMALIZED_WITH_BACKEND`, and `NONE`, `SELECTED`, or `ALL_SAFE` header exposure. Always preserve the backend HTTP outcome once the request reaches storage.

## Consequences

Compatibility and abstraction can coexist per route. Sensitive and hop-by-hop headers remain filtered, and SDK adapters may reconstruct equivalent rather than byte-identical native error envelopes.

