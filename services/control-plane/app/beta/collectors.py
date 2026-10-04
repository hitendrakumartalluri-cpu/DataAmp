from __future__ import annotations
import json
import os
import posixpath
import tempfile
from pathlib import Path, PurePosixPath

from .destinations import Destination


def collect(service, tenant, pid, request):
    profile = service.profile(tenant, pid)
    source = profile["config"].get("source", {})
    kind = source.get("kind", "API")
    if kind == "MOUNT":
        return service.collect_mount(tenant, pid, request["manifest"])
    if kind == "SFTP":
        return sftp(service, tenant, pid, source, request["manifest"])
    if kind == "OBJECT":
        return objects(service, tenant, pid, source, request)
    raise ValueError("API source accepts upload submissions; configure a collector source")


def sftp(service, tenant, pid, source, manifest):
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if source.get("known_hosts"):
        client.load_host_keys(source["known_hosts"])
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(source["host"], port=int(source.get("port", 22)), username=source["username"],
        password=os.environ.get(source.get("password_env", "")), key_filename=source.get("key_file"), timeout=15)
    root = posixpath.normpath(source["root"])

    def remote_path(name):
        value = posixpath.normpath(posixpath.join(root, name))
        if not value.startswith(root.rstrip("/") + "/"):
            raise ValueError("source path escapes configured SFTP root")
        return value

    try:
        with client.open_sftp() as remote:
            path = remote_path(manifest)
            remote.stat(path + ".ready")
            with remote.open(path, "r") as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
                if len(data) > 4 * 1024 * 1024:
                    raise ValueError("manifest exceeds 4 MiB")
                body = json.loads(data)
            if body.get("schema_version") != 1 or len(body.get("items", [])) > 10000:
                raise ValueError("invalid manifest")
            results = []
            for entry in body["items"]:
                try:
                    source_path = remote_path(entry["path"])
                    before = remote.stat(source_path)
                    if before.st_size > int(os.getenv("AMP_MAX_UPLOAD_BYTES", str(1024 ** 3))):
                        raise ValueError("source file exceeds configured limit")
                    with tempfile.NamedTemporaryFile(dir=service.root) as temp:
                        remote.get(source_path, temp.name)
                        after = remote.stat(source_path)
                        if (before.st_size, before.st_mtime) != (after.st_size, after.st_mtime):
                            raise ValueError("source changed during collection")
                        result = service.submit(tenant, pid, temp.name, entry["path"], entry["business_id"], str(entry["revision"]), entry.get("metadata", {}), entry["idempotency_key"], body["batch_id"], expected_sha256=entry.get("sha256"))
                    results.append(service.item(tenant, result["id"]))
                except Exception as exc:
                    results.append({"name": entry.get("path"), "archive_status": "FAILED", "error": str(exc)})
            return {"batch_id": body["batch_id"], "items": results}
    finally:
        client.close()


def objects(service, tenant, pid, source, request):
    group = service.catalog.get_catalogue_group(source["group_id"])
    if group["tenant_id"] != tenant:
        raise PermissionError("source belongs to another tenant")
    dest = Destination(service.catalog.backend_record_for_group(source["group_id"]))
    keys = request.get("keys", [])
    if not keys or len(keys) > 1000:
        raise ValueError("provide 1..1000 explicit object keys; bulk inventory uses discovery checkpoints")
    results = []
    for entry in keys:
        try:
            key = entry["key"]
            if key.startswith(".amp/") or key.startswith("amp-archive/"):
                raise ValueError("control/managed archive objects cannot recursively enter intake")
            stat = dest.head(key, entry.get("version", ""))
            if not stat:
                raise ValueError("source object missing")
            if stat["bytes"] > int(os.getenv("AMP_MAX_UPLOAD_BYTES", str(1024 ** 3))):
                raise ValueError("source object exceeds configured limit")
            with tempfile.NamedTemporaryFile(dir=service.root) as temp:
                checksum = dest.read_to(key, entry.get("version") or stat["version"], temp.name)
                if entry.get("sha256") and checksum != entry["sha256"]:
                    raise ValueError("source checksum mismatch")
                after = dest.head(key, entry.get("version") or stat["version"])
                if after != stat:
                    raise ValueError("source revision changed during collection")
                results.append(service.submit(tenant, pid, temp.name, key, entry["business_id"], entry.get("revision") or stat["version"] or checksum,
                    entry.get("metadata", {}), entry["idempotency_key"], request.get("batch_id", "")))
        except Exception as exc:
            results.append({"name": entry.get("key"), "archive_status": "FAILED", "error": str(exc)})
    return {"items": results}
