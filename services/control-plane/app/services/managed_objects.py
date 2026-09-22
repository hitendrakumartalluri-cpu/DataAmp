from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from ..db import Database
from .catalog import CatalogService, now
from .storage import backend_from_record, BackendOperationError, ObjectStat, BackendResponseMeta

AMP_SYSTEM_PREFIX = ".amp/"
AMP_PACKAGE_LAYOUT_V3 = "AMP_PACKAGE_V3"
AMP_PACKAGE_LAYOUT_V2 = "AMP_PACKAGE_V2"
AMP_PACKAGE_LAYOUT_V1 = "AMP_PACKAGE_V1"
ANNOTATION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class ManagedObjectService:
    """Canonical AMP managed-object service used by protocol adapters.

    Logical object identity remains the client key. Physical storage uses a reserved
    package namespace so arbitrary S3 keys can never collide with AMP internals:

      AMP-managed placement:
        .amp/objects/<hash>/<hash>/<package-id>/
          payload
          annotations/<name>.<ext>
          manifest.json

      Client-path placement:
        <logical-client-key>/<package-id>/
          payload
          annotations/<name>.<ext>
          manifest.json

    The package interior is identical in both modes. `package-id` is deterministic
    for (catalogue group, logical key) and survives protocol changes and overwrites.
    """

    def __init__(self, db: Database, catalog: CatalogService):
        self.db = db
        self.catalog = catalog

    @staticmethod
    def clean_key(path: str) -> str:
        key = (path or "").lstrip("/")
        if not key or key.endswith("/"):
            raise ValueError("object key is required")
        if ".." in key.split("/"):
            raise ValueError("invalid object key")
        if key.startswith(AMP_SYSTEM_PREFIX):
            raise ValueError("reserved AMP object prefix")
        return key

    @staticmethod
    def annotation_name(name: str) -> str:
        name = (name or "").strip()
        if not ANNOTATION_NAME_RE.fullmatch(name):
            raise ValueError("annotation name must match [A-Za-z0-9._-]{1,128}")
        return name

    def route(self, tenant: str, namespace: str) -> tuple[dict[str, Any], dict[str, Any], Any]:
        route = self.catalog.get_gateway_route(tenant, namespace)
        group = self.catalog.get_catalogue_group(str(route["catalogue_group_id"]))
        if group["state"] != "ACTIVE":
            raise ValueError(f"catalogue group is {group['state']}")
        backend = backend_from_record(self.catalog.backend_record_for_group(str(group["id"])))
        return route, group, backend

    @staticmethod
    def routed_key(route: dict[str, Any], object_path: str) -> str:
        prefix = str(route.get("object_prefix") or "")
        return prefix + ManagedObjectService.clean_key(object_path)

    def package_id(self, group: dict[str, Any], logical_key: str) -> str:
        ns = self.catalog._uuid_namespace(group["recon_namespace"])
        return str(uuid.uuid5(ns, f"amp-package|{logical_key}"))

    def package_keys(self, group: dict[str, Any], logical_key: str) -> tuple[str, str, str]:
        pid = self.package_id(group, logical_key)
        mode = self.catalog.validate_package_placement_mode(group.get("package_placement_mode") or "AMP_MANAGED_HASH")
        if mode == "CLIENT_PATH":
            root = f"{logical_key.rstrip('/')}/{pid}"
        else:
            prefix = self.catalog.validate_package_root_prefix(group.get("package_root_prefix") or ".amp/objects")
            levels = max(0, min(int(group.get("package_hash_levels") or 2), 4))
            width = max(1, min(int(group.get("package_hash_segment_chars") or 2), 4))
            compact = pid.replace("-", "")
            segments = [compact[i * width:(i + 1) * width] for i in range(levels)]
            parts = [prefix, *[x for x in segments if x], pid]
            root = "/".join(parts)
        return root, f"{root}/payload", f"{root}/manifest.json"

    @staticmethod
    def _backend_dict(stat: ObjectStat | None) -> dict[str, Any]:
        if not stat:
            return {}
        return {
            "status": stat.backend.status,
            "headers": stat.backend.headers,
            "code": stat.backend.code,
            "request_id": stat.backend.request_id,
            "raw": stat.backend.raw,
            "native_version_id": stat.version_id,
            "etag": stat.etag,
            "checksum_sha256": stat.checksum_sha256,
            "compliance": stat.compliance,
        }

    @staticmethod
    def annotation_extension(content_type: str) -> str:
        ct = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
        if ct in {"application/json", "text/json"} or ct.endswith("+json"):
            return ".json"
        if ct in {"application/xml", "text/xml"} or ct.endswith("+xml"):
            return ".xml"
        if ct == "text/csv":
            return ".csv"
        if ct.startswith("text/"):
            return ".txt"
        return ".bin"

    def sidecar_key(self, tenant: str, obj: dict[str, Any], annotation_name: str,
                    content_type: str) -> str:
        layout = str(obj.get("storage_layout") or "").upper()
        root = str(obj.get("package_root") or "").rstrip("/")
        if layout in {AMP_PACKAGE_LAYOUT_V3, AMP_PACKAGE_LAYOUT_V2} and root:
            return f"{root}/annotations/{annotation_name}{self.annotation_extension(content_type)}"
        if layout == AMP_PACKAGE_LAYOUT_V1 and root:
            return f"{root}/annotations/{annotation_name}{self.annotation_extension(content_type)}"
        return f"{AMP_SYSTEM_PREFIX}annotations/{tenant}/{obj['recon_id']}/{annotation_name}"

    def _write_manifest(self, obj: dict[str, Any], backend: Any) -> None:
        if str(obj.get("storage_layout") or "").upper() not in {AMP_PACKAGE_LAYOUT_V3, AMP_PACKAGE_LAYOUT_V2, AMP_PACKAGE_LAYOUT_V1}:
            return
        manifest_key = str(obj.get("manifest_key") or "")
        if not manifest_key:
            return
        annotations = self.catalog.list_annotations(str(obj["id"]))
        package_root = str(obj.get("package_root") or "").rstrip("/")
        logical_path = str(obj.get("object_key") or "").strip("/")
        placement_mode = "CLIENT_PATH" if package_root.startswith(logical_path + "/") else "AMP_MANAGED_HASH"
        manifest = {
            "manifestVersion": 3 if str(obj.get("storage_layout") or "").upper() == AMP_PACKAGE_LAYOUT_V3 else (2 if str(obj.get("storage_layout") or "").upper() == AMP_PACKAGE_LAYOUT_V2 else 1),
            "storageLayout": str(obj.get("storage_layout")),
            "placementMode": placement_mode,
            "packageId": package_root.rsplit("/", 1)[-1],
            "objectId": str(obj["id"]),
            "reconId": str(obj["recon_id"]),
            "logicalPath": str(obj["object_key"]),
            "logicalName": str(obj.get("logical_name") or str(obj["object_key"]).rsplit("/", 1)[-1]),
            "packageRoot": str(obj.get("package_root") or ""),
            "payload": {
                "key": self.catalog.payload_key_for(obj),
                "contentType": obj.get("content_type") or "application/octet-stream",
                "sizeBytes": int(obj.get("size_bytes") or 0),
                "etag": obj.get("etag") or "",
                "checksumSha256": obj.get("checksum_sha256"),
                "versionId": obj.get("version_id") or "",
            },
            "annotations": [
                {
                    "name": a["annotation_name"],
                    "key": a["sidecar_key"],
                    "contentType": a["content_type"],
                    "sizeBytes": int(a.get("size_bytes") or 0),
                    "checksumSha256": a.get("checksum_sha256"),
                    "version": int(a.get("annotation_version") or 1),
                    "nativeVersionId": a.get("native_version_id") or "",
                    "payloadNativeVersionId": a.get("payload_native_version_id") or "",
                }
                for a in annotations
                if str(a.get("state") or "ACTIVE").upper() == "ACTIVE"
            ],
            "updatedAt": now(),
        }
        backend.put(manifest_key, json.dumps(manifest, indent=2, sort_keys=True, default=str).encode("utf-8"), "application/json")

    def put_object(self, tenant: str, namespace: str, object_path: str, data: bytes,
                   content_type: str = "application/octet-stream", *, protocol: str = "AMP_NATIVE",
                   metadata: dict[str, str] | None = None, tags: dict[str, str] | None = None) -> dict[str, Any]:
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        existing = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        package_root, payload_key, manifest_key = self.package_keys(group, logical_key)
        if existing and str(existing.get("storage_layout") or "").upper().startswith("AMP_PACKAGE"):
            # Existing logical object always reuses the same package root/physical keys. This lets
            # the backend create its own native versions without AMP inventing payload-v1/v2 keys.
            package_root = str(existing.get("package_root") or package_root)
            payload_key = str(existing.get("payload_key") or f"{package_root}/payload")
            manifest_key = str(existing.get("manifest_key") or f"{package_root}/manifest.json")

        # For a brand-new object only, a provisional manifest prevents storage event races from
        # leaking package members into the business catalogue. On overwrite we never touch package
        # metadata before the backend accepts the authoritative payload write.
        wrote_provisional = False
        if not existing:
            provisional = {
                "manifestVersion": 3, "storageLayout": AMP_PACKAGE_LAYOUT_V3, "state": "WRITING",
                "placementMode": str(group.get("package_placement_mode") or "AMP_MANAGED_HASH"),
                "packageId": self.package_id(group, logical_key), "logicalPath": logical_key,
                "packageRoot": package_root, "payload": {"key": payload_key, "contentType": content_type},
            }
            backend.put(manifest_key, json.dumps(provisional, sort_keys=True).encode("utf-8"), "application/json")
            wrote_provisional = True
        try:
            stat = backend.put(payload_key, data, content_type)
            write_backend = stat.backend
            try:
                observed = backend.head(payload_key, stat.version_id or "")
                if observed:
                    observed.checksum_sha256 = observed.checksum_sha256 or stat.checksum_sha256
                    # Verify object state with HEAD but preserve the authoritative PUT
                    # response metadata for the client-facing Gateway response.
                    observed.backend = write_backend
                    stat = observed
            except Exception:
                pass
        except Exception:
            if wrote_provisional:
                try: backend.delete(manifest_key)
                except Exception: pass
            raise

        storage_meta = {
            "gateway_namespace": namespace, "gateway_protocol": protocol,
            "storage_layout": AMP_PACKAGE_LAYOUT_V3,
            "package_placement_mode": str(group.get("package_placement_mode") or "AMP_MANAGED_HASH"),
            "backend_response": stat.backend.raw,
            "backend_status": stat.backend.status,
            "backend_request_id": stat.backend.request_id,
            "backend_compliance": stat.compliance,
        }
        obj = self.catalog.upsert_catalogue_object(
            group_id=str(group["id"]), object_key=logical_key, version_id=stat.version_id or "",
            size_bytes=stat.size, etag=stat.etag, checksum=stat.checksum_sha256,
            content_type=content_type or stat.content_type, source_mode="GATEWAY",
            storage_layout=AMP_PACKAGE_LAYOUT_V3, package_root=package_root,
            payload_key=payload_key, manifest_key=manifest_key, metadata=storage_meta,
        )
        self.catalog.link_annotations_to_payload_version(str(obj["id"]), stat.version_id or "")
        if metadata:
            self.put_annotation(tenant, namespace, object_path, "s3-metadata",
                                json.dumps(metadata, sort_keys=True).encode("utf-8"), "application/json")
        if tags:
            self.put_annotation(tenant, namespace, object_path, "s3-tags",
                                json.dumps(tags, sort_keys=True).encode("utf-8"), "application/json")
        obj = self.catalog.get_catalogue_object(str(obj["id"]))
        self._write_manifest(obj, backend)
        self.catalog.audit(
            tenant, f"{protocol}_OBJECT_PUT", group_id=str(group["id"]), object_id=str(obj["id"]), recon_id=str(obj["recon_id"]),
            details={"namespace": namespace, "logical_object_key": logical_key, "payload_key": payload_key,
                     "manifest_key": manifest_key, "size": len(data), "native_version_id": stat.version_id or ""},
        )
        obj["_backend"] = self._backend_dict(stat)
        obj["_route"] = route
        return obj

    def put_to_group(self, group_id: str, logical_key: str, data: bytes,
                     content_type: str = "application/octet-stream", *, source_mode: str = "MIGRATED",
                     origin_recon_id: str | None = None, annotations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Write a logical object to a target Catalogue Group using that group's package policy.

        Used by migration/hydration. The backend remains authoritative for native versions,
        retention, WORM and lifecycle. AMP only records the resulting backend state.
        """
        group = self.catalog.get_catalogue_group(group_id)
        if group["state"] != "ACTIVE":
            raise ValueError(f"catalogue group is {group['state']}")
        backend = backend_from_record(self.catalog.backend_record_for_group(group_id))
        logical_key = self.clean_key(logical_key)
        existing = self.catalog.find_current_catalogue_object(group_id, logical_key)
        package_root, payload_key, manifest_key = self.package_keys(group, logical_key)
        if existing and str(existing.get("storage_layout") or "").upper().startswith("AMP_PACKAGE"):
            package_root = str(existing.get("package_root") or package_root)
            payload_key = str(existing.get("payload_key") or f"{package_root}/payload")
            manifest_key = str(existing.get("manifest_key") or f"{package_root}/manifest.json")

        wrote_provisional = False
        if not existing:
            provisional = {
                "manifestVersion": 3, "storageLayout": AMP_PACKAGE_LAYOUT_V3, "state": "WRITING",
                "placementMode": str(group.get("package_placement_mode") or "AMP_MANAGED_HASH"),
                "packageId": self.package_id(group, logical_key), "logicalPath": logical_key,
                "packageRoot": package_root, "payload": {"key": payload_key, "contentType": content_type},
            }
            backend.put(manifest_key, json.dumps(provisional, sort_keys=True).encode("utf-8"), "application/json")
            wrote_provisional = True
        try:
            stat = backend.put(payload_key, data, content_type)
            write_backend = stat.backend
            try:
                observed = backend.head(payload_key, stat.version_id or "")
                if observed:
                    observed.checksum_sha256 = observed.checksum_sha256 or stat.checksum_sha256
                    # Verify object state with HEAD but preserve the authoritative PUT
                    # response metadata for the client-facing Gateway response.
                    observed.backend = write_backend
                    stat = observed
            except Exception:
                pass
        except Exception:
            if wrote_provisional:
                try: backend.delete(manifest_key)
                except Exception: pass
            raise

        obj = self.catalog.upsert_catalogue_object(
            group_id=group_id, object_key=logical_key, version_id=stat.version_id or "",
            size_bytes=stat.size, etag=stat.etag, checksum=stat.checksum_sha256,
            content_type=content_type or stat.content_type, source_mode=source_mode, origin_recon_id=origin_recon_id,
            storage_layout=AMP_PACKAGE_LAYOUT_V3, package_root=package_root, payload_key=payload_key, manifest_key=manifest_key,
            metadata={"backend_response": stat.backend.raw, "backend_status": stat.backend.status,
                      "backend_request_id": stat.backend.request_id, "backend_compliance": stat.compliance},
        )
        self.catalog.link_annotations_to_payload_version(str(obj["id"]), stat.version_id or "")

        for item in annotations or []:
            name = self.annotation_name(str(item.get("name") or ""))
            ct = str(item.get("content_type") or "application/octet-stream")
            body = bytes(item.get("data") or b"")
            previous = None
            try: previous = self.catalog.get_annotation(str(obj["id"]), name, include_deleted=True)
            except KeyError: pass
            sidecar_key = str((previous or {}).get("sidecar_key") or f"{package_root}/annotations/{name}{self.annotation_extension(ct)}")
            astat = backend.put(sidecar_key, body, ct)
            self.catalog.upsert_annotation(str(obj["id"]), name, sidecar_key, ct, len(body),
                                           hashlib.sha256(body).hexdigest(), astat.version_id or "", astat.backend.raw)

        obj = self.catalog.get_catalogue_object(str(obj["id"]))
        self._write_manifest(obj, backend)
        obj["_backend"] = self._backend_dict(stat)
        return obj

    def get_object(self, tenant: str, namespace: str, object_path: str, version_id: str = "") -> tuple[bytes, dict[str, Any]]:
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        if not obj or str(obj.get("lifecycle_state") or "").upper() == "TOMBSTONED":
            # Package key is deterministic, so ask the authoritative backend even if Catalogue is stale.
            _, payload_key, _ = self.package_keys(group, logical_key)
        else:
            payload_key = self.catalog.payload_key_for(obj)
        data, stat = backend.get_with_stat(payload_key, version_id)
        if not obj:
            obj = self.catalog.upsert_catalogue_object(group_id=str(group["id"]), object_key=logical_key,
                version_id=stat.version_id or "", size_bytes=stat.size, etag=stat.etag,
                checksum=stat.checksum_sha256, content_type=stat.content_type, source_mode="GATEWAY",
                storage_layout=AMP_PACKAGE_LAYOUT_V3, package_root=payload_key.rsplit("/",1)[0],
                payload_key=payload_key, manifest_key=payload_key.rsplit("/",1)[0]+"/manifest.json",
                metadata={"backend_response":stat.backend.raw,"backend_compliance":stat.compliance})
        obj["_backend"] = self._backend_dict(stat); obj["_route"] = route
        return data, obj

    def head_object(self, tenant: str, namespace: str, object_path: str, version_id: str = "") -> tuple[dict[str, Any], Any]:
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        payload_key = self.catalog.payload_key_for(obj) if obj else self.package_keys(group, logical_key)[1]
        stat = backend.head(payload_key, version_id)
        if not stat:
            raise BackendOperationError("object not found", status=404, code="NotFound")
        if not obj:
            obj = self.catalog.upsert_catalogue_object(group_id=str(group["id"]), object_key=logical_key,
                version_id=stat.version_id or "", size_bytes=stat.size, etag=stat.etag, checksum=stat.checksum_sha256,
                content_type=stat.content_type, source_mode="GATEWAY", storage_layout=AMP_PACKAGE_LAYOUT_V3,
                package_root=payload_key.rsplit("/",1)[0], payload_key=payload_key,
                manifest_key=payload_key.rsplit("/",1)[0]+"/manifest.json",
                metadata={"backend_response":stat.backend.raw,"backend_compliance":stat.compliance})
        obj["_backend"] = self._backend_dict(stat); obj["_route"] = route
        return obj, stat

    def delete_object(self, tenant: str, namespace: str, object_path: str, *, protocol: str = "AMP_NATIVE") -> dict[str, Any]:
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        payload_key = self.catalog.payload_key_for(obj) if obj else self.package_keys(group, logical_key)[1]

        # Backend compliance/lifecycle is authoritative. Payload delete is attempted first; if
        # retention/WORM/versioning rules reject it, no AMP sidecar or manifest is touched.
        meta = backend.delete(payload_key)
        if obj:
            for annotation in self.catalog.list_annotations(str(obj["id"])):
                try:
                    backend.delete(str(annotation["sidecar_key"]))
                    self.catalog.mark_annotation_deleted(str(obj["id"]), str(annotation["annotation_name"]))
                except BackendOperationError:
                    # Payload outcome remains authoritative; leave orphan sidecar visible to reconciliation.
                    pass
            manifest_key = str(obj.get("manifest_key") or "")
            if manifest_key:
                try: backend.delete(manifest_key)
                except BackendOperationError: pass
        tomb = self.catalog.tombstone_by_key(str(group["id"]), logical_key, "", source_mode="GATEWAY")
        self.catalog.audit(tenant, f"{protocol}_OBJECT_DELETE", group_id=str(group["id"]), object_id=str(tomb["id"]),
                           recon_id=str(tomb["recon_id"]), details={"namespace": namespace, "logical_object_key": logical_key})
        tomb["_backend"] = {"status":meta.status,"headers":meta.headers,"code":meta.code,"request_id":meta.request_id,"raw":meta.raw}
        tomb["_route"] = route
        return tomb

    def put_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str,
                       data: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
        annotation_name = self.annotation_name(annotation_name)
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        if not obj or str(obj.get("lifecycle_state") or "").upper() != "ACTIVE":
            raise FileNotFoundError(logical_key)
        previous = None
        try:
            previous = self.catalog.get_annotation(str(obj["id"]), annotation_name, include_deleted=True)
        except KeyError:
            pass
        # Once an annotation exists, always reuse the same physical sidecar key. Backend-native
        # versioning then owns annotation history; AMP never creates legal-v1/legal-v2 keys.
        sidecar_key = str(previous.get("sidecar_key") or "") if previous else ""
        if not sidecar_key:
            sidecar_key = self.sidecar_key(tenant, obj, annotation_name, content_type)
        stat = backend.put(sidecar_key, data, content_type)
        checksum = hashlib.sha256(data).hexdigest()
        annotation = self.catalog.upsert_annotation(str(obj["id"]), annotation_name, sidecar_key, content_type, len(data), checksum, stat.version_id or "", stat.backend.raw)
        obj = self.catalog.get_catalogue_object(str(obj["id"]))
        self._write_manifest(obj, backend)
        return {**annotation, "object_id": str(obj["id"]), "recon_id": str(obj["recon_id"]), "_backend": self._backend_dict(stat), "_route": route}

    def get_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str,
                       payload_version_id: str = "") -> tuple[bytes, dict[str, Any], dict[str, Any]]:
        annotation_name = self.annotation_name(annotation_name)
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        if not obj:
            raise FileNotFoundError(logical_key)
        try:
            annotation = self.catalog.get_annotation(str(obj["id"]), annotation_name)
        except KeyError as exc:
            raise FileNotFoundError(annotation_name) from exc
        annotation_native_version = ""
        if payload_version_id:
            annotation_native_version = self.catalog.annotation_version_for_payload(
                str(obj["id"]), annotation_name, payload_version_id
            )
            # A managed payload version with no snapshot link means AMP never observed this
            # annotation for that version; do not silently return the current annotation.
            if not annotation_native_version:
                raise FileNotFoundError(f"annotation {annotation_name} unavailable for payload version {payload_version_id}")
        data, stat = backend.get_with_stat(str(annotation["sidecar_key"]), annotation_native_version)
        annotation["resolved_payload_version_id"] = payload_version_id or str(obj.get("version_id") or "")
        annotation["resolved_native_version_id"] = stat.version_id or annotation_native_version
        annotation["_backend"] = self._backend_dict(stat); annotation["_route"] = route
        return data, obj, annotation

    def head_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str,
                        payload_version_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        annotation_name = self.annotation_name(annotation_name)
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        if not obj:
            raise FileNotFoundError(logical_key)
        try:
            annotation = self.catalog.get_annotation(str(obj["id"]), annotation_name)
        except KeyError as exc:
            raise FileNotFoundError(annotation_name) from exc
        annotation_native_version = ""
        if payload_version_id:
            annotation_native_version = self.catalog.annotation_version_for_payload(str(obj["id"]), annotation_name, payload_version_id)
            if not annotation_native_version:
                raise FileNotFoundError(f"annotation {annotation_name} unavailable for payload version {payload_version_id}")
        stat = backend.head(str(annotation["sidecar_key"]), annotation_native_version)
        if not stat:
            raise FileNotFoundError(annotation_name)
        annotation["resolved_payload_version_id"] = payload_version_id or str(obj.get("version_id") or "")
        annotation["resolved_native_version_id"] = stat.version_id or annotation_native_version
        annotation["_backend"] = self._backend_dict(stat); annotation["_route"] = route
        return obj, annotation

    def delete_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str) -> dict[str, Any]:
        annotation_name = self.annotation_name(annotation_name)
        route, group, backend = self.route(tenant, namespace)
        logical_key = self.routed_key(route, object_path)
        obj = self.catalog.find_current_catalogue_object(str(group["id"]), logical_key)
        if not obj:
            raise FileNotFoundError(logical_key)
        try:
            annotation = self.catalog.get_annotation(str(obj["id"]), annotation_name)
        except KeyError as exc:
            raise FileNotFoundError(annotation_name) from exc
        backend.delete(str(annotation["sidecar_key"]))
        deleted = self.catalog.mark_annotation_deleted(str(obj["id"]), annotation_name)
        obj = self.catalog.get_catalogue_object(str(obj["id"]))
        self._write_manifest(obj, backend)
        self.catalog.audit(tenant, "ANNOTATION_DELETE", group_id=str(group["id"]), object_id=str(obj["id"]),
                           recon_id=str(obj["recon_id"]), details={"annotation": annotation_name})
        return deleted

    def get_json_annotation(self, object_id: str, annotation_name: str) -> dict[str, Any]:
        obj = self.catalog.get_catalogue_object(object_id)
        try:
            ann = self.catalog.get_annotation(object_id, annotation_name)
        except KeyError:
            return {}
        backend = backend_from_record(self.catalog.backend_record_for_group(str(obj["catalogue_group_id"])))
        try:
            return json.loads(backend.get(str(ann["sidecar_key"])).decode("utf-8"))
        except Exception:
            return {}
