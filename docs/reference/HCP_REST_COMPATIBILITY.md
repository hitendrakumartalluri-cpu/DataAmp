# HCP REST Compatibility — AMP 0.9.0-beta.5.0

AMP provides a controlled HCP-style REST compatibility surface over the canonical managed-object service.

## Supported beta operations

```text
PUT    /rest/{tenant}/{namespace}/{object-path}
GET    /rest/{tenant}/{namespace}/{object-path}
HEAD   /rest/{tenant}/{namespace}/{object-path}
DELETE /rest/{tenant}/{namespace}/{object-path}
```

Custom metadata:

```text
PUT    ...?type=custom-metadata&annotation=<name>
GET    ...?type=custom-metadata&annotation=<name>
HEAD   ...?type=custom-metadata&annotation=<name>
DELETE ...?type=custom-metadata&annotation=<name>
```

Explicit backend payload version retrieval:

```text
GET /rest/.../obj?versionId=<native-version-id>
```

For managed annotations, adding the same `versionId` resolves the annotation snapshot AMP recorded for that payload version. AMP does not store duplicate version payloads; it retrieves native backend versions.

## Package semantics

Logical HCP paths remain client-facing object identities. Managed data is stored as `AMP_PACKAGE_V3` with either AMP-managed hash distribution or client-path placement. The package GUID and physical payload/annotation keys remain stable across overwrites so native backend versioning can operate naturally.

## Backend authority

HCP/backend-native versioning, retention, WORM, legal hold and lifecycle remain authoritative. AMP does not pre-enforce or emulate these features. If the backend rejects an overwrite/delete, AMP keeps that backend HTTP outcome and presents it according to the Gateway Route response policy.

## Response policy

Per route:

- `RAW_BACKEND`
- `AMP_NORMALIZED`
- `AMP_NORMALIZED_WITH_BACKEND`

and header policy:

- `NONE`
- `SELECTED`
- `ALL_SAFE`

See `GATEWAY_RESPONSE_POLICY.md`.

## Compatibility statement

This beta is **not** a full HCP REST clone. A formal compatibility matrix against a real HCP appliance/VM is still required before making a production compatibility claim. HCP MQE/search APIs are separate from the Gateway API and belong to Catalogue discovery/change-capture connectors.
