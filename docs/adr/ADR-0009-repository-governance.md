# ADR-0009: GitHub Is the Canonical Source of Truth

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Features, decisions, code, and status had become fragmented across chats and packaged artifacts.

## Decision

The `DataAmp` GitHub repository is authoritative. Durable product documentation lives in `docs/`, implementation in `services/`, production packaging in `deployments/`, and all lab-only instructions, scripts, manifests, and topology in `lab/`. GitHub Issues and Projects hold live delivery status; ADRs hold accepted decisions.

## Consequences

Chat memory is not a product record. Meaningful changes must update the repository artifacts together. Lab assumptions must not leak into product services.

