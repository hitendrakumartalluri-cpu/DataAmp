# ADR-0005: Container-Local Identity and AMP Package V3

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Global identity and earlier package layouts caused cross-container coupling and possible collisions with valid client keys.

## Decision

Derive `recon_id` and package GUID deterministically with UUIDv5 from a Catalogue Group namespace and logical key. Store one stable package containing `payload`, `manifest.json`, and `annotations/`. Support AMP-managed hash and client-path placement while preserving the same interior.

## Consequences

Protocol switching and normal overwrites reuse the package. Migrated targets receive target-local identities and retain source lineage. Backend-native versioning, not synthetic payload filenames, holds history.

