# AMP S3 Client Compatibility — 0.9.0-beta.5.0.6

AMP exposes a path-style S3-compatible beta front door backed by the same canonical managed-object service used by HCP REST.

## Implemented beta subset

- AWS Signature V4 header authentication using lab-configured S3 credentials
- PutObject
- GetObject
- HeadObject
- DeleteObject
- ListObjectsV2
- Range GET
- `x-amz-meta-*` mapped to managed `s3-metadata` sidecar
- `x-amz-tagging` mapped to managed `s3-tags` sidecar
- GetObjectTagging
- explicit `versionId` GET/HEAD where the backend exposes native versions
- cross-protocol HCP REST <-> S3 reads

## Native versioning

AMP writes the same stable physical payload key on overwrite. Backend S3 versioning, if enabled, creates native VersionIds. AMP catalogues those VersionIds and does not create `payload-v1`/`payload-v2` keys or manage pruning.

## Compliance/WORM

AMP does not implement S3 Object Lock, retention, legal hold or lifecycle policy enforcement. The target backend remains authoritative. Where state is exposed by the SDK, AMP can catalogue it for visibility/reconciliation.

## Response policy

Gateway Route configuration controls RAW vs normalized presentation and safe backend headers. RAW mode preserves the backend HTTP outcome and SDK-visible S3 error semantics; normalized mode returns stable AMP `HTTP_STATUS_n` codes while keeping the same HTTP status.

## Deferred

- multipart upload
- CopyObject
- presigned URLs
- full ListObjectVersions/version-management surface
- Object Lock configuration APIs
- bucket management/control-plane APIs

These are intentionally outside the beta P0 compatibility subset.
