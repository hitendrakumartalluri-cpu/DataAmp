# ADR-0002: Stable Virtual Catalogue Sharding

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

Catalogue storage must scale and rebalance without changing object identity or requiring clients to understand physical partitions.

## Decision

Each Catalogue Group uses 1024 stable virtual shards derived from the recon ID. Contiguous virtual-shard ranges map to movable physical shards.

## Consequences

Physical placement can evolve while logical routing remains stable. Source enumeration must use inventory, events, or prefix/range partitioning; every hash shard must not independently LIST the whole container.

