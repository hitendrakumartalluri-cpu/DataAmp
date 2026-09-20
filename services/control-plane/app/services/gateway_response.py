from __future__ import annotations
import json
from typing import Any
from .storage import BackendOperationError

HOP_BY_HOP = {"connection","keep-alive","proxy-authenticate","proxy-authorization","te","trailer","transfer-encoding","upgrade"}
SENSITIVE = {"authorization","set-cookie","server","via"}
SELECTED = {
    "etag","last-modified","content-type","content-length","accept-ranges","content-range",
    "x-amz-version-id","x-amz-checksum-sha256","x-amz-checksum-crc32","x-amz-checksum-crc32c",
    "x-amz-request-id","x-amz-id-2","x-minio-deployment-id","x-hcp-request-id",
}


def filter_headers(headers: dict[str, Any] | None, policy: str) -> dict[str, str]:
    mode = str(policy or "SELECTED").upper()
    if mode == "NONE": return {}
    out: dict[str,str] = {}
    for k,v in (headers or {}).items():
        lk=str(k).lower()
        if lk in HOP_BY_HOP or lk in SENSITIVE: continue
        if mode == "SELECTED" and lk not in SELECTED: continue
        out[lk]=str(v)
    return out


def amp_error_code(status: int) -> str:
    return f"HTTP_STATUS_{int(status)}"


def normalized_error(status: int, request_id: str, *, backend: BackendOperationError | None = None,
                     include_backend: bool = False) -> dict[str, Any]:
    body: dict[str,Any] = {"httpStatus": int(status), "error": amp_error_code(status), "requestId": request_id}
    if include_backend and backend:
        body["backend"] = {"status": backend.status, "code": backend.code, "requestId": backend.request_id}
    return body


def normalized_s3_error_xml(status: int, request_id: str, *, backend: BackendOperationError | None = None,
                            include_backend: bool = False, resource: str = "") -> str:
    import html
    extra = ""
    if include_backend and backend:
        extra = (f"<BackendStatus>{backend.status}</BackendStatus>"
                 f"<BackendCode>{html.escape(backend.code)}</BackendCode>"
                 f"<BackendRequestId>{html.escape(backend.request_id)}</BackendRequestId>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<Error><Code>{amp_error_code(status)}</Code><Message>{amp_error_code(status)}</Message>'
            f'<Resource>{html.escape(resource)}</Resource><RequestId>{html.escape(request_id)}</RequestId>{extra}</Error>')


def raw_error_body(exc: BackendOperationError, protocol: str, resource: str = "") -> tuple[str, str]:
    # boto3 exposes parsed backend error semantics rather than exact wire bytes. Re-serialize
    # faithfully enough for beta proxy behavior; future native HCP adapter can preserve raw bytes.
    if protocol.upper() == "S3":
        import html
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                f'<Error><Code>{html.escape(exc.code)}</Code><Message>{html.escape(str(exc))}</Message>'
                f'<Resource>{html.escape(resource)}</Resource><RequestId>{html.escape(exc.request_id)}</RequestId></Error>',
                'application/xml')
    if exc.body:
        return exc.body, "text/plain"
    # Preserve the backend adapter's meaningful error message when no raw body exists.
    # Local/test adapters and some SDKs provide no wire body but do provide a useful
    # exception message. Only fall back to the raw metadata envelope when neither is
    # available.
    message = str(exc).strip()
    if message:
        return message, "text/plain"
    return json.dumps(exc.raw, default=str), "application/json"
