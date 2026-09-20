# Gateway Response Policy — AMP beta.5

Response presentation is configurable per Gateway Route (client-facing namespace/S3 bucket mapping).

## Response modes

### RAW_BACKEND

Preserve backend HTTP status and backend application-layer error semantics as closely as the adapter can observe them. Pass configured safe backend headers. AMP may strip hop-by-hop transport headers and may add `X-AMP-Request-ID` when enabled.

For SDK-backed S3/HCP-S3 adapters, the beta reconstructs an equivalent error envelope from the provider SDK response because the original wire bytes are not always exposed by the SDK.

### AMP_NORMALIZED

Preserve the backend HTTP status but return a vendor-neutral AMP error.

Example:

```json
{
  "httpStatus": 404,
  "error": "HTTP_STATUS_404",
  "requestId": "..."
}
```

The intent is one stable application contract across HCP, AWS, MinIO and other backends.

### AMP_NORMALIZED_WITH_BACKEND

Same normalized AMP response plus bounded backend diagnostics (status/code/request ID).

## Backend header policy

- `NONE`: do not expose backend headers beyond protocol-required AMP response headers.
- `SELECTED` (recommended default): expose useful data-plane headers such as ETag, VersionId, checksums, Last-Modified, Content-Type/Length, ranges and backend request IDs.
- `ALL_SAFE`: expose end-to-end backend headers except hop-by-hop and explicitly sensitive/internal headers.

## Backend response capture

`capture_backend_response=true` stores bounded metadata/error information in AMP's administrative transaction history even when the application receives a normalized response.

AMP never stores successful payload bodies in this transaction history.

## Success responses

For normalized modes AMP exposes canonical useful values when the backend provides them:

- ETag
- native VersionId
- SHA/checksum headers where available
- Last-Modified
- Content-Type / Content-Length
- Range headers
- backend request ID (normalized-with-backend)
- AMP request ID

AMP does not invent a native VersionId if the backend is unversioned.
