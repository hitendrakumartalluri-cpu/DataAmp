# Governance and Compliance Module

## Responsibility

Turns AMP's identity, metadata, search and backend observations into explainable policy decisions and audit evidence.

## Current Beta 5

- Backend compliance-state observation where supported.
- Backend-authority rule documented and enforced architecturally.
- Audit and backend transaction history foundations.
- Migration/reconciliation evidence foundations.
- AMP does not enforce WORM, retention or legal hold itself.

## Complete-product scope

- Query-time authorization maps with deny precedence.
- Lifecycle-bound search and retrieval audit history.
- PII scan policies and protected results.
- Rule-based retention and case-based legal hold.
- Native backend action/verification adapters.
- Compliance evidence packs and chain of custody.
- Duplicate/encrypted-document detection.
- Policy-based storage optimisation.
- Explainable decisions, approval workflow and exception reporting.

## Boundaries

Solr may discover candidates but cannot be final compliance authority. PostgreSQL records policy state and evidence; the storage backend enforces native controls.

Tracking: [authorization #18](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/18), [audit #21](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/21), [retention/hold #22](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/22).
