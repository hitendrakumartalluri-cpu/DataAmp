# Gateway Module

## Responsibility

The Gateway is AMP's client-facing compatibility and routing plane. It accepts protocol-specific requests, resolves a Gateway Route, invokes the canonical managed-object service and delegates physical operations to a storage adapter.

## Current Beta 5

- HCP REST object PUT/GET/HEAD/DELETE.
- HCP custom-metadata operations.
- S3 Put/Get/Head/Delete/ListObjectsV2 subset, range reads, metadata and tags.
- Lab SigV4 validation.
- Cross-protocol HCP/S3 access to one logical object.
- Per-route raw or normalized responses.
- Safe backend-header filtering and bounded backend transaction capture.
- Native VersionId observation and historic-version retrieval.

## Request flow

```text
Client -> HCP/S3 adapter -> Gateway Route -> ManagedObjectService
       -> Storage adapter -> Authoritative backend
       -> Catalogue/audit registration -> Client response
```

## Boundaries

The backend decides whether a write, overwrite or delete is accepted. AMP does not replace native versioning, WORM, retention, legal hold, lifecycle, replication or durability.

## Complete-product scope

Multipart S3, CopyObject, presigned URLs, expanded version APIs, production HCP integration, Azure/GCS adapters, unified namespace, enterprise identity/RBAC, rate limiting, quotas, WAN/HA certification and performance evidence.

## Primary code

- `services/control-plane/app/main.py`
- `services/control-plane/app/services/hcp_gateway.py`
- `services/control-plane/app/services/s3_gateway.py`
- `services/control-plane/app/services/gateway_response.py`
- `services/control-plane/app/services/storage.py`

Tracking: [Gateway umbrella issue #23](https://github.com/hitendrakumartalluri-cpu/DataAmp/issues/23).
