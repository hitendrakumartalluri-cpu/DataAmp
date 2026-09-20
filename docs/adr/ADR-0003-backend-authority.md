# ADR-0003: Backend-Authoritative Storage Semantics

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Duplicating native storage controls in AMP would create conflicting authorities and false compliance guarantees.

## Decision

Backends own native versions and pruning, WORM, retention, legal hold, lifecycle, replication, durability, encryption, and operation acceptance. AMP observes, translates, catalogues, migrates, reconciles, and reports those states but never emulates unsupported compliance enforcement in PostgreSQL.

## Consequences

Adapters must expose capabilities explicitly. Unsupported equivalence is reported as unsupported rather than simulated.

