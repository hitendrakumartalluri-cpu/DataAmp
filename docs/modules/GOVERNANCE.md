# Governance and Compliance Module

## Responsibility

Turns AMP's identity, metadata, search and backend observations into explainable policy decisions and audit evidence.

## Safety model

- Solr identifies policy candidates; the connector verifies the current source object before action.
- Policies support dry-run, approval, immutable versions and explicit conflict resolution.
- Storage remains the enforcement authority for retention, Object Lock and holds.
- AMP records action, response, verification and exceptions.

## Complete-product scope

- Query-time authorization maps with deny precedence.
- Lifecycle-bound search and retrieval audit history.
- Versioned user regex tables with field scope, validation, time limits and protected match evidence.
- Rule-based retention and case-based legal hold.
- Native backend action/verification adapters.
- Compliance evidence packs and chain of custody.
- Duplicate/encrypted-document detection.
- Policy-based storage optimisation.
- Explainable decisions, approval workflow and exception reporting.

## Boundaries

Solr may discover candidates but cannot be final compliance authority. PostgreSQL records policy state and evidence; the storage backend enforces native controls. Regex detection is not a guarantee of complete PII discovery and must report rule coverage and scan failures.

Tracking: [authorization #18](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/18), [audit #21](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/21), [retention/hold #22](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/22).
