# ADR-0010: Authentication Is Parked for Beta 5

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Beta 5 focuses on object semantics, catalogue boundaries, migration, versions, and response policy. A partial enterprise identity implementation would create misleading security confidence.

## Decision

Beta 5 uses lab-only API/S3 credentials. OIDC, LDAP/AD, RBAC, production secret handling, and policy integration are deferred to Beta 6 and are mandatory before production certification.

## Consequences

Beta 5 must remain in controlled lab environments. Documentation and UI must not imply production authentication readiness.

