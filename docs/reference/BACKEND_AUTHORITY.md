# Backend Authority Principle — AMP beta.5

## Non-negotiable boundary

AMP is a compatibility, translation, routing, catalogue, migration and reconciliation layer. It is **not** a compliance storage engine.

The authoritative backend storage owns:

- native object/version IDs
- physical versioning and version pruning
- WORM / immutable storage
- retention
- legal hold / Object Lock
- lifecycle / storage-class transitions
- replication / erasure coding / durability
- encryption at rest
- deletion and overwrite acceptance/rejection
- backend IAM/ACL when a backend-native access model is selected

AMP owns:

- client protocol compatibility (HCP REST, S3 subset, AMP APIs)
- logical object identity and routing
- configurable physical package placement
- rich annotation/metadata sidecars
- administrative catalogue and native-version inventory
- source/target version mapping and migration evidence
- change capture, reconciliation and audit
- search/AI preparation and downstream reconciliation

## Versions

A logical AMP object has one stable package ID and one stable physical payload key.

```text
<GUID>/
  payload
  annotations/
  manifest.json
```

When a client writes the same logical object again, AMP writes the **same backend key**. If backend versioning is enabled, the backend creates the next native version. AMP records the native VersionId and does not create `payload-v1`, `payload-v2`, or a new package.

If backend versioning is disabled, AMP does not invent history.

AMP maintains an administrative mapping between payload versions and the annotation versions observed for that payload snapshot. That mapping contains no duplicate payload bytes and does not control pruning.

## Compliance state

Where the backend exposes retention/hold/Object Lock state, AMP can catalogue and display it and can compare source vs target during reconciliation. It does not become the enforcement authority.

For a migration AMP may:

1. read source compliance state,
2. request a native equivalent on the target when a supported target adapter is implemented,
3. read target state back,
4. report MATCH / MISMATCH / UNSUPPORTED.

AMP must never emulate a regulatory feature in PostgreSQL when the target storage does not natively provide it.

## Error/outcome authority

The backend decides whether a retention, lock, hold or lifecycle action is accepted. AMP records the native response and verifies resulting state; it never converts a failed native action into a successful compliance outcome.

Example: backend returns 409 because an overwrite is prohibited. AMP returns HTTP 409 in every response mode. RAW mode preserves backend semantics; normalized mode gives an AMP generic error such as `HTTP_STATUS_409`.
