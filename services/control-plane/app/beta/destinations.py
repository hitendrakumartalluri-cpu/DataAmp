from __future__ import annotations
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def key_path(root, key):
    root = Path(root).resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("destination key escapes its root")
    return path


class Destination:
    """Archive writes are separate from read-only discovery connectors."""
    def __init__(self, record):
        self.record = record
        self.kind = record["kind"].upper()
        self.options = record.get("options", {})
        self.s3 = None
        self.azure = None
        if self.kind in {"S3", "AWS_S3", "MINIO", "HCP_S3", "VSP_ONE_OBJECT"}:
            from ..services.storage import S3Storage
            self.s3 = S3Storage(record)
        elif self.kind in {"AZURE", "AZURE_BLOB"}:
            from ..services.storage import AzureBlobStorage
            self.azure = AzureBlobStorage(record)
        elif self.kind != "LOCAL":
            raise ValueError("Native HCP REST archive writes are not qualified; use an explicitly configured HCP_S3 adapter")

    def validate_policy(self, policy):
        if not policy:
            return
        if set(policy) - {"retention_until", "mode", "legal_hold"}:
            raise ValueError("unsupported archive protection option")
        if not policy.get("retention_until") and not policy.get("legal_hold"):
            raise ValueError("specify retention_until or legal_hold")
        if not self.s3 or not self.options.get("archive_object_lock_verified"):
            raise ValueError("required native protection is not qualified for this destination")
        if policy.get("retention_until") and policy.get("mode") not in {"GOVERNANCE", "COMPLIANCE"}:
            raise ValueError("retention mode must be GOVERNANCE or COMPLIANCE")
        if policy.get("retention_until"):
            from datetime import datetime, timezone
            until = datetime.fromisoformat(policy["retention_until"].replace("Z", "+00:00"))
            if until.tzinfo is None or until <= datetime.now(timezone.utc):
                raise ValueError("retention_until must be a future timezone-aware date")

    def head(self, key, version=""):
        if self.kind == "LOCAL":
            path = key_path(self.record["root_path"], key)
            if not path.is_file():
                return None
            return {"version": "", "bytes": path.stat().st_size, "etag": str(path.stat().st_mtime_ns)}
        stat = (self.s3 or self.azure).head(key, version)
        return None if stat is None else {"version": stat.version_id, "bytes": stat.size, "etag": stat.etag}

    def read_to(self, key, version, target):
        with Path(target).open("wb") as out:
            if self.kind == "LOCAL":
                with key_path(self.record["root_path"], key).open("rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
            elif self.s3:
                args = {"Bucket": self.s3.bucket, "Key": key}
                if version:
                    args["VersionId"] = version
                response = self.s3.client.get_object(**args)
                try:
                    for chunk in iter(lambda: response["Body"].read(1024 * 1024), b""):
                        out.write(chunk)
                finally:
                    response["Body"].close()
            else:
                blob = self.azure.client.get_blob_client(key, version_id=version or None)
                for chunk in blob.download_blob().chunks():
                    out.write(chunk)
        return digest(target)

    def write(self, key, path, content_type, policy):
        self.validate_policy(policy)
        if self.kind == "LOCAL":
            target = key_path(self.record["root_path"], key)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with Path(path).open("rb") as inp, tmp.open("xb") as out:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                # Refuse replacement of an existing logical archive artifact.
                os.link(tmp, target)
                fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                tmp.unlink(missing_ok=True)
            return self.head(key)
        if self.s3:
            extra = {"ContentType": content_type, "Metadata": {"amp-sha256": digest(path)}}
            if policy.get("retention_until"):
                from datetime import datetime
                extra.update(ObjectLockMode=policy["mode"], ObjectLockRetainUntilDate=datetime.fromisoformat(policy["retention_until"].replace("Z", "+00:00")))
            if policy.get("legal_hold"):
                extra["ObjectLockLegalHoldStatus"] = "ON"
            from boto3.s3.transfer import TransferConfig
            # SDK retries/resumes parts within the attempt. A process crash restarts
            # from durable staging; completed objects are reconciled before retry.
            self.s3.client.upload_file(str(path), self.s3.bucket, key, ExtraArgs=extra,
                Config=TransferConfig(max_concurrency=4, multipart_threshold=8 * 1024 * 1024))
            return self.head(key)
        from azure.storage.blob import ContentSettings
        with Path(path).open("rb") as inp:
            self.azure.client.get_blob_client(key).upload_blob(inp, overwrite=False,
                content_settings=ContentSettings(content_type=content_type), metadata={"amp_sha256": digest(path)})
        return self.head(key)

    def verify_policy(self, key, version, policy):
        if not policy:
            return {"required": False, "verified": False, "mode": "INTEGRITY_ONLY"}
        self.validate_policy(policy)
        args = {"Bucket": self.s3.bucket, "Key": key}
        if version:
            args["VersionId"] = version
        evidence = {"required": True, "verified": True}
        if policy.get("retention_until"):
            from datetime import datetime
            actual = self.s3.client.get_object_retention(**args)["Retention"]
            wanted = datetime.fromisoformat(policy["retention_until"].replace("Z", "+00:00"))
            if actual.get("Mode") != policy["mode"] or actual["RetainUntilDate"] < wanted:
                raise ValueError("native retention verification failed")
            evidence["retention"] = {"mode": actual["Mode"], "until": actual["RetainUntilDate"].isoformat()}
        if policy.get("legal_hold"):
            actual = self.s3.client.get_object_legal_hold(**args)["LegalHold"]
            if actual.get("Status") != "ON":
                raise ValueError("native legal hold verification failed")
            evidence["legal_hold"] = "ON"
        return evidence
