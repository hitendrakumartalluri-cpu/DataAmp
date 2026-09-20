# ADR-0006: Register Targets Only After Physical Write

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Registering a target before persistence can make the catalogue claim that unavailable data exists.

## Decision

Batch migration and read-through hydration register a target catalogue record only after the target physical write succeeds. Source and target catalogues remain independent; migration links preserve lineage.

## Consequences

Failures leave no false-positive target record. Retry and orphan detection must handle physical writes whose acknowledgement is lost before registration.

