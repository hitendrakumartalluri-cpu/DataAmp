from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, unquote_plus

from fastapi import Request

from ..config import settings
from ..db import Database
from .catalog import CatalogService
from .managed_objects import ManagedObjectService


class S3AuthError(Exception):
    pass


class S3GatewayService:
    """Pragmatic S3-compatible beta adapter over AMP's canonical object service.

    Supported: SigV4 header auth, Put/Get/Head/DeleteObject, ListObjectsV2,
    Range GET, x-amz-meta-* ingestion and x-amz-tagging ingestion.
    """

    def __init__(self, db: Database, catalog: CatalogService):
        self.db = db
        self.catalog = catalog
        self.objects = ManagedObjectService(db, catalog)

    # ---------- authentication ----------
    @staticmethod
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def verify_sigv4(self, request: Request, body: bytes = b"") -> str:
        """Validate ordinary Authorization-header AWS Signature V4.

        Presigned URL auth is intentionally deferred. When AMP_S3_SECRET_KEY is
        empty the beta accepts unsigned requests for local/unit-test use.
        """
        if not settings.s3_secret_key:
            return settings.default_tenant
        auth = request.headers.get("authorization", "")
        if not auth.startswith("AWS4-HMAC-SHA256 "):
            raise S3AuthError("AccessDenied")
        try:
            attrs = {}
            for part in auth[len("AWS4-HMAC-SHA256 "):].split(","):
                k, v = part.strip().split("=", 1)
                attrs[k] = v
            credential = attrs["Credential"]
            signed_headers = attrs["SignedHeaders"]
            supplied = attrs["Signature"]
            access_key, date_scope, region, service, terminal = credential.split("/", 4)
            if access_key != settings.s3_access_key or terminal != "aws4_request" or service != "s3":
                raise S3AuthError("InvalidAccessKeyId")
            amz_date = request.headers.get("x-amz-date")
            if not amz_date:
                raise S3AuthError("AccessDenied")

            raw_path = request.scope.get("raw_path") or request.url.path.encode("utf-8")
            path = raw_path.decode("latin-1")
            # Canonical URI: preserve existing % escapes and slash separators.
            canonical_uri = quote(path, safe="/%-_.~")
            pairs = parse_qsl(request.url.query, keep_blank_values=True)
            enc = [(quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in pairs]
            canonical_query = "&".join(f"{k}={v}" for k, v in sorted(enc))

            header_names = signed_headers.split(";")
            canonical_headers = ""
            for name in header_names:
                value = request.headers.get(name, "")
                value = " ".join(value.strip().split())
                canonical_headers += f"{name.lower()}:{value}\n"

            payload_hash = request.headers.get("x-amz-content-sha256") or hashlib.sha256(body).hexdigest()
            if payload_hash == "UNSIGNED-PAYLOAD":
                canonical_payload_hash = payload_hash
            else:
                actual = hashlib.sha256(body).hexdigest()
                if payload_hash != actual:
                    raise S3AuthError("XAmzContentSHA256Mismatch")
                canonical_payload_hash = payload_hash

            canonical_request = "\n".join([
                request.method, canonical_uri, canonical_query, canonical_headers,
                signed_headers, canonical_payload_hash,
            ])
            scope = f"{date_scope}/{region}/s3/aws4_request"
            string_to_sign = "\n".join([
                "AWS4-HMAC-SHA256", amz_date, scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ])
            k_date = self._sign(("AWS4" + settings.s3_secret_key).encode("utf-8"), date_scope)
            k_region = self._sign(k_date, region)
            k_service = self._sign(k_region, "s3")
            k_signing = self._sign(k_service, "aws4_request")
            expected = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, supplied):
                raise S3AuthError("SignatureDoesNotMatch")
            return settings.s3_tenant or settings.default_tenant
        except S3AuthError:
            raise
        except Exception as exc:
            raise S3AuthError("AuthorizationHeaderMalformed") from exc

    # ---------- helpers ----------
    def _route(self, tenant: str, bucket: str):
        return self.objects.route(tenant, bucket)

    def _logical_from_catalogue(self, route: dict[str, Any], key: str) -> str:
        prefix = str(route.get("object_prefix") or "")
        if prefix and key.startswith(prefix):
            return key[len(prefix):]
        return key

    def user_metadata(self, obj: dict[str, Any]) -> dict[str, str]:
        raw = self.objects.get_json_annotation(str(obj["id"]), "s3-metadata")
        return {str(k): str(v) for k, v in raw.items()}

    def user_tags(self, obj: dict[str, Any]) -> dict[str, str]:
        raw = self.objects.get_json_annotation(str(obj["id"]), "s3-tags")
        return {str(k): str(v) for k, v in raw.items()}

    def put(self, tenant: str, bucket: str, key: str, data: bytes, content_type: str,
            metadata: dict[str, str], tags: dict[str, str]) -> dict[str, Any]:
        return self.objects.put_object(tenant, bucket, key, data, content_type, protocol="S3",
                                       metadata=metadata, tags=tags)

    def get(self, tenant: str, bucket: str, key: str, version_id: str = ""):
        return self.objects.get_object(tenant, bucket, key, version_id)

    def head(self, tenant: str, bucket: str, key: str, version_id: str = ""):
        return self.objects.head_object(tenant, bucket, key, version_id)

    def delete(self, tenant: str, bucket: str, key: str):
        try:
            return self.objects.delete_object(tenant, bucket, key, protocol="S3")
        except FileNotFoundError:
            return None  # S3 DeleteObject is idempotent.

    def list_v2(self, tenant: str, bucket: str, *, prefix: str = "", max_keys: int = 1000,
                continuation_token: str | None = None, delimiter: str = "") -> str:
        route, group, _ = self._route(tenant, bucket)
        routed_prefix = str(route.get("object_prefix") or "") + prefix
        cursor = ""
        if continuation_token:
            try:
                cursor = base64.urlsafe_b64decode(continuation_token.encode("ascii") + b"===").decode("utf-8")
            except Exception:
                cursor = ""
        sql = """SELECT object_key,size_bytes,etag,updated_at FROM catalogue_objects
                 WHERE catalogue_group_id=? AND lifecycle_state='ACTIVE' AND object_key LIKE ?"""
        params: list[Any] = [str(group["id"]), routed_prefix + "%"]
        if cursor:
            sql += " AND object_key>?"; params.append(str(route.get("object_prefix") or "") + cursor)
        sql += " ORDER BY object_key ASC LIMIT ?"; params.append(min(max(int(max_keys), 1), 1000) + 1)
        rows = self.db.fetchall(sql, params)
        truncated = len(rows) > max_keys
        rows = rows[:max_keys]
        contents = []
        common_prefixes: set[str] = set()
        for r in rows:
            key = self._logical_from_catalogue(route, str(r["object_key"]))
            if delimiter and delimiter in key[len(prefix):]:
                rest = key[len(prefix):]
                cp = prefix + rest.split(delimiter, 1)[0] + delimiter
                common_prefixes.add(cp)
                continue
            contents.append(
                f"<Contents><Key>{html.escape(key)}</Key><LastModified>{html.escape(str(r.get('updated_at') or ''))}</LastModified>"
                f"<ETag>&quot;{html.escape(str(r.get('etag') or ''))}&quot;</ETag><Size>{int(r.get('size_bytes') or 0)}</Size>"
                f"<StorageClass>STANDARD</StorageClass></Contents>"
            )
        next_token = ""
        if truncated and rows:
            logical = self._logical_from_catalogue(route, str(rows[-1]["object_key"]))
            next_token = base64.urlsafe_b64encode(logical.encode("utf-8")).decode("ascii").rstrip("=")
        cp_xml = "".join(f"<CommonPrefixes><Prefix>{html.escape(x)}</Prefix></CommonPrefixes>" for x in sorted(common_prefixes))
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<Name>{html.escape(bucket)}</Name><Prefix>{html.escape(prefix)}</Prefix>"
            f"<KeyCount>{len(contents)}</KeyCount><MaxKeys>{max_keys}</MaxKeys><IsTruncated>{str(truncated).lower()}</IsTruncated>"
            + "".join(contents) + cp_xml
            + (f"<NextContinuationToken>{html.escape(next_token)}</NextContinuationToken>" if next_token else "")
            + "</ListBucketResult>"
        )

    @staticmethod
    def error_xml(code: str, message: str, resource: str = "") -> str:
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                f"<Error><Code>{html.escape(code)}</Code><Message>{html.escape(message)}</Message>"
                f"<Resource>{html.escape(resource)}</Resource></Error>")
