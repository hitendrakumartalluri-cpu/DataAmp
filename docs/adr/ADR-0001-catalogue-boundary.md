# ADR-0001: Catalogue Group Boundary

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

A global object catalogue creates lifecycle, scale, ownership, and decommission coupling across unrelated storage containers.

## Decision

One storage system plus one namespace/bucket/container forms exactly one logical Catalogue Group. Each group owns its generations, shards, jobs, reconciliation, freeze/archive, and decommission lifecycle.

## Consequences

Operations and failures are independently scoped. Cross-container identity is expressed through lineage rather than a global object row.

