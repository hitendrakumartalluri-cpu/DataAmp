from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Any
import hashlib


@dataclass
class BackendResponseMeta:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    code: str = ""
    request_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class BackendOperationError(Exception):
    """Authoritative backend operation failure.

    AMP response policy decides whether this is passed through or normalized; the
    storage decision itself is never replaced by AMP compliance/lifecycle logic.
    """
    def __init__(self, message: str, *, status: int = 502, code: str = "BackendError",
                 headers: dict[str, str] | None = None, request_id: str = "",
                 raw: dict[str, Any] | None = None, body: str = ""):
        super().__init__(message)
        self.status = int(status or 502)
        self.code = str(code or "BackendError")
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.request_id = str(request_id or "")
        self.raw = raw or {}
        self.body = body or ""


@dataclass
class ObjectStat:
    key: str
    size: int
    etag: str = ""
    version_id: str = ""
    content_type: str = "application/octet-stream"
    checksum_sha256: str | None = None
    backend: BackendResponseMeta = field(default_factory=BackendResponseMeta)
    compliance: dict[str, Any] = field(default_factory=dict)
    is_current: bool | None = None


class StorageBackend:
    def list(self, prefix: str = "") -> Iterator[ObjectStat]: raise NotImplementedError
    def head(self, key: str, version_id: str = "") -> ObjectStat | None: raise NotImplementedError
    def get(self, key: str, version_id: str = "") -> bytes: return self.get_with_stat(key, version_id)[0]
    def get_with_stat(self, key: str, version_id: str = "") -> tuple[bytes, ObjectStat]: raise NotImplementedError
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> ObjectStat: raise NotImplementedError
    def delete(self, key: str, version_id: str = "") -> BackendResponseMeta: raise NotImplementedError
    def list_versions(self, key: str) -> list[ObjectStat]: return []


class LocalFilesystemStorage(StorageBackend):
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        clean = key.lstrip("/").replace("..", "__")
        path = (self.root / clean).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("invalid key")
        return path

    def list(self, prefix: str = ""):
        base = self._path(prefix) if prefix else self.root
        if base.is_file():
            yield self._stat(base); return
        if not base.exists(): return
        for p in sorted(base.rglob("*")):
            if p.is_file(): yield self._stat(p)

    def _stat(self, path: Path) -> ObjectStat:
        rel = path.relative_to(self.root).as_posix(); st = path.stat()
        return ObjectStat(rel, st.st_size, etag=f'{st.st_mtime_ns:x}-{st.st_size:x}',
                          content_type=_guess_content_type(rel),
                          backend=BackendResponseMeta(status=200, headers={"content-length": str(st.st_size)}))

    def head(self, key: str, version_id: str = ""):
        p = self._path(key); return self._stat(p) if p.is_file() else None

    def get_with_stat(self, key: str, version_id: str = ""):
        p = self._path(key)
        if not p.is_file():
            raise BackendOperationError("object not found", status=404, code="NotFound")
        return p.read_bytes(), self._stat(p)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> ObjectStat:
        p = self._path(key); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        stat = self._stat(p); stat.content_type = content_type; stat.checksum_sha256 = hashlib.sha256(data).hexdigest()
        stat.backend = BackendResponseMeta(status=200, headers={"etag": stat.etag})
        return stat

    def delete(self, key: str, version_id: str = "") -> BackendResponseMeta:
        p = self._path(key)
        if not p.exists(): raise BackendOperationError("object not found", status=404, code="NotFound")
        p.unlink(); return BackendResponseMeta(status=204)


class S3Storage(StorageBackend):
    def __init__(self, cfg: dict):
        import boto3
        self.bucket = cfg.get("bucket")
        kwargs: dict[str, Any] = {}
        if cfg.get("endpoint"): kwargs["endpoint_url"] = cfg["endpoint"]
        if cfg.get("region"): kwargs["region_name"] = cfg["region"]
        if cfg.get("access_key"): kwargs["aws_access_key_id"] = cfg["access_key"]
        if cfg.get("secret_key"): kwargs["aws_secret_access_key"] = cfg["secret_key"]
        self.client = boto3.client("s3", **kwargs)

    @staticmethod
    def _meta(resp: dict[str, Any], status_default: int = 200) -> BackendResponseMeta:
        rm = resp.get("ResponseMetadata") or {}
        headers = {str(k).lower(): str(v) for k, v in (rm.get("HTTPHeaders") or {}).items()}
        return BackendResponseMeta(status=int(rm.get("HTTPStatusCode") or status_default), headers=headers,
                                   request_id=str(rm.get("RequestId") or headers.get("x-amz-request-id") or ""),
                                   raw={k: v for k, v in resp.items() if k != "Body"})

    @staticmethod
    def _raise(exc: Exception) -> None:
        response = getattr(exc, "response", {}) or {}
        err = response.get("Error") or {}
        rm = response.get("ResponseMetadata") or {}
        headers = {str(k).lower(): str(v) for k, v in (rm.get("HTTPHeaders") or {}).items()}
        status = int(rm.get("HTTPStatusCode") or 502)
        code = str(err.get("Code") or "BackendError")
        msg = str(err.get("Message") or str(exc))
        raise BackendOperationError(msg, status=status, code=code, headers=headers,
                                    request_id=str(rm.get("RequestId") or headers.get("x-amz-request-id") or ""),
                                    raw=response, body=msg) from exc

    def list(self, prefix: str = ""):
        token = None
        while True:
            args = {"Bucket": self.bucket, "Prefix": prefix}
            if token: args["ContinuationToken"] = token
            try: resp = self.client.list_objects_v2(**args)
            except Exception as e: self._raise(e)
            for item in resp.get("Contents", []):
                yield ObjectStat(item["Key"], item["Size"], etag=str(item.get("ETag", "")).strip('"'))
            if not resp.get("IsTruncated"): break
            token = resp.get("NextContinuationToken")

    def head(self, key: str, version_id: str = ""):
        args: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version_id: args["VersionId"] = version_id
        try:
            r = self.client.head_object(**args)
            compliance = {k: r.get(k) for k in ("ObjectLockMode", "ObjectLockRetainUntilDate", "ObjectLockLegalHoldStatus", "ServerSideEncryption", "StorageClass") if r.get(k) is not None}
            return ObjectStat(key, r["ContentLength"], etag=str(r.get("ETag", "")).strip('"'),
                              version_id=r.get("VersionId", "") or "", content_type=r.get("ContentType", "application/octet-stream"),
                              checksum_sha256=r.get("ChecksumSHA256"), backend=self._meta(r), compliance=compliance)
        except Exception as e:
            response = getattr(e, "response", {}) or {}; rm=response.get("ResponseMetadata") or {}; err=response.get("Error") or {}
            if int(rm.get("HTTPStatusCode") or 0) == 404 or str(err.get("Code") or "") in {"404","NoSuchKey","NotFound","NoSuchVersion"}:
                return None
            self._raise(e)

    def get_with_stat(self, key: str, version_id: str = ""):
        args: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version_id: args["VersionId"] = version_id
        try:
            r = self.client.get_object(**args); data = r["Body"].read()
            compliance = {k: r.get(k) for k in ("ObjectLockMode", "ObjectLockRetainUntilDate", "ObjectLockLegalHoldStatus", "ServerSideEncryption", "StorageClass") if r.get(k) is not None}
            stat = ObjectStat(key, len(data), etag=str(r.get("ETag", "")).strip('"'), version_id=r.get("VersionId", "") or "",
                              content_type=r.get("ContentType", "application/octet-stream"), checksum_sha256=r.get("ChecksumSHA256"),
                              backend=self._meta(r), compliance=compliance)
            return data, stat
        except Exception as e: self._raise(e)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> ObjectStat:
        try:
            r = self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
            meta = self._meta(r)
            return ObjectStat(key, len(data), etag=str(r.get("ETag", "")).strip('"'), version_id=r.get("VersionId", "") or "",
                              content_type=content_type, checksum_sha256=hashlib.sha256(data).hexdigest(), backend=meta)
        except Exception as e: self._raise(e)

    def delete(self, key: str, version_id: str = "") -> BackendResponseMeta:
        args: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version_id: args["VersionId"] = version_id
        try:
            r = self.client.delete_object(**args)
            return self._meta(r, 204)
        except Exception as e: self._raise(e)

    def list_versions(self, key: str) -> list[ObjectStat]:
        out: list[ObjectStat] = []
        key_marker = version_marker = None
        while True:
            args: dict[str, Any] = {"Bucket": self.bucket, "Prefix": key}
            if key_marker: args["KeyMarker"] = key_marker
            if version_marker: args["VersionIdMarker"] = version_marker
            try: r = self.client.list_object_versions(**args)
            except Exception as e: self._raise(e)
            meta = self._meta(r)
            for item in r.get("Versions", []):
                if item.get("Key") != key: continue
                out.append(ObjectStat(key, int(item.get("Size") or 0), etag=str(item.get("ETag", "")).strip('"'),
                                      version_id=str(item.get("VersionId") or ""), backend=meta,
                                      is_current=bool(item.get("IsLatest"))))
            if not r.get("IsTruncated"): break
            key_marker, version_marker = r.get("NextKeyMarker"), r.get("NextVersionIdMarker")
        return out


def _guess_content_type(key: str) -> str:
    import mimetypes
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


def backend_from_record(rec: dict) -> StorageBackend:
    kind = rec["kind"].upper()
    if kind == "LOCAL": return LocalFilesystemStorage(rec.get("root_path") or rec.get("endpoint") or ".")
    if kind in {"S3", "AWS_S3", "MINIO", "HCP_S3"}: return S3Storage(rec)
    raise ValueError(f"Unsupported storage kind: {kind}")
