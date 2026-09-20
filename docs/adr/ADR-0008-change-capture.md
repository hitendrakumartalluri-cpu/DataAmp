# ADR-0008: Event-Backed Change Capture with Baseline Reconciliation

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Objects can change outside AMP, so gateway-only ledger updates cannot establish catalogue completeness.

## Decision

Use source-native notifications or change feeds through raw adapter topics into a normalized Kafka event contract. Combine deltas with periodic storage baselines, generation marking, idempotency, stale-event suppression, DLQ, and replay.

## Consequences

The catalogue observes external writes and deletes. Events improve freshness but never replace completeness scans or inventory baselines.

