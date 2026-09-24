from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import uuid
from typing import Any

from ..db import Database

AMP_RECON_ROOT = uuid.UUID("2f93ccf8-5bf5-4f39-92c0-809f0e9f6ec4")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return str(uuid.uuid4())


class CatalogService:
    """Administrative catalogue registry and per-container manifest logic.

    A catalogue group maps 1:1 to a physical storage container:
    storage-system + namespace/bucket/directory. Objects do not span catalogue groups.
    Cross-container relationships are represented by migration_links/origin_recon_id.
    """

    def __init__(self, db: Database):
        self.db = db

    def audit(self, tenant: str, action: str, *, group_id: str | None = None,
              object_id: str | None = None, recon_id: str | None = None,
              outcome: str = "SUCCESS", details: dict | None = None,
              actor: str = "amp-system", request_id: str | None = None):
        self.db.execute(
            "INSERT INTO audit_events(id,tenant_id,catalogue_group_id,actor,action,catalogue_object_id,recon_id,request_id,outcome,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (uid(), tenant, group_id, actor, action, object_id, recon_id, request_id, outcome,
             self.db.dumps(details or {}), now()),
        )

    def emit(self, tenant: str, group_id: str | None, aggregate_type: str,
             aggregate_id: str, event_type: str, payload: dict):
        self.db.execute(
            "INSERT INTO outbox_events(id,tenant_id,catalogue_group_id,aggregate_type,aggregate_id,event_type,payload_json,status,attempts,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid(), tenant, group_id, aggregate_type, aggregate_id, event_type,
             self.db.dumps(payload), "PENDING", 0, now()),
        )

    # ---------- storage/control plane ----------
    def create_storage(self, data: dict) -> dict:
        sid = data.get("id") or uid()
        ts = now()
        self.db.execute(
            """INSERT INTO storage_systems(id,tenant_id,name,kind,role,endpoint,root_path,region,access_key,secret_key,options_json,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, data.get("tenant_id", "demo"), data["name"], data["kind"].upper(),
             data.get("role", "EXTERNAL").upper(), data.get("endpoint"), data.get("root_path"),
             data.get("region"), data.get("access_key"), data.get("secret_key"),
             self.db.dumps(data.get("options", {})), data.get("status", "ONLINE"), ts, ts),
        )
        self.audit(data.get("tenant_id", "demo"), "STORAGE_CREATE", details={"storage_id": sid, "name": data["name"]})
        return self.get_storage(sid)

    def get_storage(self, sid: str, mask_secret: bool = False) -> dict:
        rec = self.db.fetchone("SELECT * FROM storage_systems WHERE id=?", (sid,))
        if not rec:
            raise KeyError(sid)
        rec["options"] = self.db.loads(rec.pop("options_json", "{}"), {})
        if mask_secret:
            rec["secret_key"] = "••••••••" if rec.get("secret_key") else None
            rec["access_key"] = (rec.get("access_key", "")[:4] + "••••") if rec.get("access_key") else None
        return rec

    def list_storages(self, tenant: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM storage_systems WHERE tenant_id=? ORDER BY name", (tenant,))
        for r in rows:
            r["options"] = self.db.loads(r.pop("options_json", "{}"), {})
            r["catalogue_groups"] = int(self.db.scalar("SELECT COUNT(*) c FROM catalogue_groups WHERE storage_id=?", (r["id"],), 0) or 0)
            r["secret_key"] = "••••••••" if r.get("secret_key") else None
            r["access_key"] = (r.get("access_key", "")[:4] + "••••") if r.get("access_key") else None
        return rows

    # ---------- catalogue groups and shards ----------
    def create_catalogue_group(self, *, tenant: str, storage_id: str, container_name: str,
                               container_type: str = "S3_BUCKET", name: str | None = None,
                               virtual_shards: int = 1024, physical_shards: int = 1,
                               package_placement_mode: str = "AMP_MANAGED_HASH",
                               package_root_prefix: str = ".amp/objects",
                               package_hash_levels: int = 2,
                               package_hash_segment_chars: int = 2) -> dict:
        self.get_storage(storage_id)
        virtual_shards = max(16, int(virtual_shards))
        physical_shards = max(1, min(int(physical_shards), virtual_shards))
        package_placement_mode = self.validate_package_placement_mode(package_placement_mode)
        package_root_prefix = self.validate_package_root_prefix(package_root_prefix)
        package_hash_levels = max(0, min(int(package_hash_levels), 4))
        package_hash_segment_chars = max(1, min(int(package_hash_segment_chars), 4))
        existing = self.db.fetchone(
            "SELECT id FROM catalogue_groups WHERE storage_id=? AND container_type=? AND container_name=?",
            (storage_id, container_type.upper(), container_name),
        )
        if existing:
            return self.get_catalogue_group(existing["id"])
        gid, ts = uid(), now()
        # The recon namespace is persistent and can be exported into HOP/source connector config.
        storage_ns = self._uuid_namespace(storage_id)
        recon_ns = str(uuid.uuid5(storage_ns, f"{container_type.upper()}|{container_name}"))
        self.db.execute(
            """INSERT INTO catalogue_groups(id,tenant_id,storage_id,name,container_type,container_name,recon_namespace,virtual_shard_count,physical_shard_count,state,package_placement_mode,package_root_prefix,package_hash_levels,package_hash_segment_chars,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gid, tenant, storage_id, name or container_name, container_type.upper(), container_name,
             recon_ns, virtual_shards, physical_shards, "ACTIVE", package_placement_mode, package_root_prefix,
             package_hash_levels, package_hash_segment_chars, ts, ts),
        )
        self._create_shards(gid, virtual_shards, physical_shards)
        self.audit(tenant, "CATALOGUE_CREATE", group_id=gid,
                   details={"storage_id": storage_id, "container": container_name, "physical_shards": physical_shards,
                            "virtual_shards": virtual_shards, "package_placement_mode": package_placement_mode,
                            "package_root_prefix": package_root_prefix, "package_hash_levels": package_hash_levels,
                            "package_hash_segment_chars": package_hash_segment_chars})
        return self.get_catalogue_group(gid)

    @staticmethod
    def validate_package_placement_mode(mode: str) -> str:
        mode = str(mode or "AMP_MANAGED_HASH").upper().strip()
        aliases = {"AMP_MANAGED": "AMP_MANAGED_HASH", "MANAGED": "AMP_MANAGED_HASH",
                   "USER_PATH": "CLIENT_PATH", "PRESERVE_PATH": "CLIENT_PATH"}
        mode = aliases.get(mode, mode)
        if mode not in {"AMP_MANAGED_HASH", "CLIENT_PATH"}:
            raise ValueError("package placement mode must be AMP_MANAGED_HASH or CLIENT_PATH")
        return mode

    @staticmethod
    def validate_package_root_prefix(prefix: str) -> str:
        prefix = str(prefix or ".amp/objects").strip().strip("/")
        if not prefix:
            prefix = ".amp/objects"
        if ".." in prefix.split("/"):
            raise ValueError("invalid package root prefix")
        return prefix

    def set_package_layout(self, gid: str, *, mode: str, root_prefix: str = ".amp/objects",
                           hash_levels: int = 2, hash_segment_chars: int = 2) -> dict:
        group = self.get_catalogue_group(gid)
        mode = self.validate_package_placement_mode(mode)
        root_prefix = self.validate_package_root_prefix(root_prefix)
        hash_levels = max(0, min(int(hash_levels), 4))
        hash_segment_chars = max(1, min(int(hash_segment_chars), 4))
        self.db.execute(
            """UPDATE catalogue_groups SET package_placement_mode=?,package_root_prefix=?,
               package_hash_levels=?,package_hash_segment_chars=?,updated_at=? WHERE id=?""",
            (mode, root_prefix, hash_levels, hash_segment_chars, now(), gid),
        )
        self.audit(group["tenant_id"], "CATALOGUE_PACKAGE_LAYOUT_CHANGE", group_id=gid, details={
            "package_placement_mode": mode, "package_root_prefix": root_prefix,
            "package_hash_levels": hash_levels, "package_hash_segment_chars": hash_segment_chars,
            "applies_to": "FUTURE_MANAGED_WRITES",
        })
        return self.get_catalogue_group(gid)

    def _create_shards(self, group_id: str, virtual_count: int, physical_count: int) -> None:
        ts = now()
        for shard_no in range(physical_count):
            start = (shard_no * virtual_count) // physical_count
            end = (((shard_no + 1) * virtual_count) // physical_count) - 1
            self.db.execute(
                "INSERT INTO catalogue_shards(id,catalogue_group_id,shard_no,virtual_start,virtual_end,physical_database,physical_schema,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (uid(), group_id, shard_no, start, end, "amp_control", f"cat_{group_id.replace('-', '')[:12]}_{shard_no:03d}", "ACTIVE", ts, ts),
            )

    def get_catalogue_group(self, gid: str) -> dict:
        rec = self.db.fetchone(
            """SELECT g.*,s.name storage_name,s.kind storage_kind,s.role storage_role,s.endpoint,s.root_path,s.region,s.status storage_status
               FROM catalogue_groups g JOIN storage_systems s ON s.id=g.storage_id WHERE g.id=?""",
            (gid,),
        )
        if not rec:
            raise KeyError(gid)
        rec["objects"] = int(self.db.scalar("SELECT COUNT(*) c FROM catalogue_objects WHERE catalogue_group_id=?", (gid,), 0) or 0)
        rec["active_objects"] = int(self.db.scalar("SELECT COUNT(*) c FROM catalogue_objects WHERE catalogue_group_id=? AND lifecycle_state='ACTIVE'", (gid,), 0) or 0)
        rec["missing_objects"] = int(self.db.scalar("SELECT COUNT(*) c FROM catalogue_objects WHERE catalogue_group_id=? AND lifecycle_state='MISSING'", (gid,), 0) or 0)
        rec["tombstones"] = int(self.db.scalar("SELECT COUNT(*) c FROM catalogue_objects WHERE catalogue_group_id=? AND lifecycle_state='TOMBSTONED'", (gid,), 0) or 0)
        rec["shards"] = self.list_shards(gid)
        rec["last_generation"] = self.db.fetchone(
            "SELECT generation_no,status,objects_seen,started_at,completed_at FROM catalogue_generations WHERE catalogue_group_id=? ORDER BY generation_no DESC LIMIT 1",
            (gid,),
        )
        return rec

    def list_catalogue_groups(self, tenant: str) -> list[dict]:
        ids = self.db.fetchall("SELECT id FROM catalogue_groups WHERE tenant_id=? ORDER BY created_at", (tenant,))
        return [self.get_catalogue_group(x["id"]) for x in ids]

    def list_shards(self, gid: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM catalogue_shards WHERE catalogue_group_id=? ORDER BY shard_no", (gid,))
        for r in rows:
            r["objects"] = int(self.db.scalar("SELECT COUNT(*) c FROM catalogue_objects WHERE shard_id=?", (r["id"],), 0) or 0)
        return rows

    def set_group_state(self, gid: str, state: str) -> dict:
        state = state.upper()
        if state not in {"ACTIVE", "FROZEN", "VERIFIED", "ARCHIVED", "DECOMMISSIONED"}:
            raise ValueError("invalid catalogue state")
        group = self.get_catalogue_group(gid)
        self.db.execute("UPDATE catalogue_groups SET state=?,updated_at=? WHERE id=?", (state, now(), gid))
        self.audit(group["tenant_id"], "CATALOGUE_STATE_CHANGE", group_id=gid, details={"state": state})
        return self.get_catalogue_group(gid)

    def backend_record_for_group(self, gid: str) -> dict:
        group = self.get_catalogue_group(gid)
        storage = self.get_storage(group["storage_id"])
        rec = dict(storage)
        if storage["kind"].upper() in {"S3", "AWS_S3", "MINIO", "HCP_S3"}:
            rec["bucket"] = group["container_name"]
        elif storage["kind"].upper() == "LOCAL" and group["container_name"] not in {"", ".", "/"}:
            from pathlib import Path
            rec["root_path"] = str(Path(storage.get("root_path") or storage.get("endpoint") or ".") / group["container_name"])
        return rec

    # ---------- annotation sidecar manifest ----------
    def upsert_annotation(self, object_id: str, annotation_name: str, sidecar_key: str,
                          content_type: str, size_bytes: int, checksum_sha256: str,
                          native_version_id: str = "", backend_response: dict | None = None) -> dict:
        obj = self.get_catalogue_object(object_id)
        existing = self.db.fetchone(
            "SELECT id,annotation_version FROM object_annotations WHERE catalogue_object_id=? AND annotation_name=?",
            (object_id, annotation_name),
        )
        ts = now()
        if existing:
            aid = existing["id"]
            revision = int(existing.get("annotation_version") or 0) + 1
            self.db.execute(
                "UPDATE object_annotations SET sidecar_key=?,content_type=?,size_bytes=?,checksum_sha256=?,annotation_version=?,native_version_id=?,payload_native_version_id=?,backend_response_json=?,state='ACTIVE',updated_at=? WHERE id=?",
                (sidecar_key, content_type, size_bytes, checksum_sha256, revision, native_version_id or "", str(obj.get("version_id") or ""), self.db.dumps(backend_response or {}), ts, aid),
            )
        else:
            aid, revision = uid(), 1
            self.db.execute(
                "INSERT INTO object_annotations(id,catalogue_object_id,annotation_name,sidecar_key,content_type,size_bytes,checksum_sha256,annotation_version,native_version_id,payload_native_version_id,backend_response_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, object_id, annotation_name, sidecar_key, content_type, size_bytes, checksum_sha256, revision,
                 native_version_id or "", str(obj.get("version_id") or ""), self.db.dumps(backend_response or {}), "ACTIVE", ts, ts),
            )
        payload_version = str(obj.get("version_id") or "")
        self.record_annotation_version(aid, native_version_id or "", payload_version, checksum_sha256, size_bytes, content_type, backend_response or {})
        self._link_annotation_snapshot(object_id, payload_version, annotation_name, native_version_id or "")
        self.audit(obj["tenant_id"], "ANNOTATION_WRITE", group_id=str(obj["catalogue_group_id"]),
                   object_id=str(obj["id"]), recon_id=str(obj["recon_id"]),
                   details={"annotation": annotation_name, "sidecar_key": sidecar_key, "amp_revision": revision,
                            "native_version_id": native_version_id or ""})
        return self.get_annotation(object_id, annotation_name)

    def get_annotation(self, object_id: str, annotation_name: str, include_deleted: bool = False) -> dict:
        sql = "SELECT * FROM object_annotations WHERE catalogue_object_id=? AND annotation_name=?"
        params: list[Any] = [object_id, annotation_name]
        if not include_deleted:
            sql += " AND state='ACTIVE'"
        row = self.db.fetchone(sql, params)
        if not row:
            raise KeyError(annotation_name)
        return row

    def list_annotations(self, object_id: str, include_deleted: bool = False) -> list[dict]:
        sql = "SELECT * FROM object_annotations WHERE catalogue_object_id=?"
        params: list[Any] = [object_id]
        if not include_deleted:
            sql += " AND state='ACTIVE'"
        sql += " ORDER BY annotation_name"
        return self.db.fetchall(sql, params)

    def mark_annotation_deleted(self, object_id: str, annotation_name: str) -> dict:
        row = self.get_annotation(object_id, annotation_name)
        self.db.execute("UPDATE object_annotations SET state='DELETED',updated_at=? WHERE id=?", (now(), row["id"]))
        return self.get_annotation(object_id, annotation_name, include_deleted=True)

    # ---------- deterministic object identity and shard routing ----------
    @staticmethod
    def _uuid_namespace(value: str | uuid.UUID) -> uuid.UUID:
        # psycopg returns PostgreSQL UUID columns as uuid.UUID objects, while
        # SQLite returns strings. Accept both so deterministic recon IDs are
        # identical across database backends.
        if isinstance(value, uuid.UUID):
            return value
        text = str(value)
        try:
            return uuid.UUID(text)
        except (ValueError, AttributeError, TypeError):
            return uuid.uuid5(AMP_RECON_ROOT, text)

    def recon_id_for(self, group: dict | str, object_key: str, version_id: str = "") -> str:
        # Stable logical identity: backend-native versions are observations of the same object.
        g = self.get_catalogue_group(group) if isinstance(group, str) else group
        ns = self._uuid_namespace(g["recon_namespace"])
        return str(uuid.uuid5(ns, object_key))

    @staticmethod
    def virtual_shard_for(recon_id: str, virtual_count: int) -> int:
        return int.from_bytes(hashlib.sha256(recon_id.encode()).digest()[:8], "big") % int(virtual_count)

    def shard_for_virtual(self, gid: str, virtual_shard: int) -> dict:
        r = self.db.fetchone(
            "SELECT * FROM catalogue_shards WHERE catalogue_group_id=? AND virtual_start<=? AND virtual_end>=?",
            (gid, virtual_shard, virtual_shard),
        )
        if not r:
            raise RuntimeError(f"no physical shard covers virtual shard {virtual_shard}")
        return r

    # ---------- generations and manifest rows ----------
    def begin_generation(self, gid: str, job_id: str, prefix: str = "") -> dict:
        group = self.get_catalogue_group(gid)
        generation_no = int(self.db.scalar("SELECT COALESCE(MAX(generation_no),0)+1 n FROM catalogue_generations WHERE catalogue_group_id=?", (gid,), 1) or 1)
        gen = {"id": uid(), "generation_no": generation_no}
        self.db.execute(
            "INSERT INTO catalogue_generations(id,catalogue_group_id,job_id,generation_no,status,scan_prefix,objects_seen,started_at) VALUES(?,?,?,?,?,?,?,?)",
            (gen["id"], gid, job_id, generation_no, "RUNNING", prefix, 0, now()),
        )
        self.audit(group["tenant_id"], "CATALOGUE_GENERATION_START", group_id=gid, details={"generation": generation_no, "prefix": prefix})
        return gen

    def finish_generation(self, gid: str, gen_id: str, objects_seen: int, prefix: str = "") -> dict:
        group = self.get_catalogue_group(gid)
        # Missing/tombstone detection is scoped to the exact prefix scanned.
        sql = "SELECT id,lifecycle_state,missing_count,recon_id,object_key FROM catalogue_objects WHERE catalogue_group_id=? AND lifecycle_state<>'TOMBSTONED' AND (last_seen_generation_id IS NULL OR last_seen_generation_id<>?)"
        params: list[Any] = [gid, gen_id]
        if prefix:
            sql += " AND object_key LIKE ?"
            params.append(prefix + "%")
        candidates = self.db.fetchall(sql, params)
        missing = tombstoned = 0
        ts = now()
        for row in candidates:
            miss = int(row.get("missing_count") or 0) + 1
            state = "TOMBSTONED" if miss >= 2 else "MISSING"
            if state == "TOMBSTONED":
                tombstoned += 1
            else:
                missing += 1
            self.db.execute(
                "UPDATE catalogue_objects SET lifecycle_state=?,missing_count=?,updated_at=?,tombstoned_at=? WHERE id=?",
                (state, miss, ts, ts if state == "TOMBSTONED" else None, row["id"]),
            )
        self.db.execute(
            "UPDATE catalogue_generations SET status='COMPLETE',objects_seen=?,completed_at=? WHERE id=?",
            (objects_seen, ts, gen_id),
        )
        self.audit(group["tenant_id"], "CATALOGUE_GENERATION_COMPLETE", group_id=gid,
                   details={"objects_seen": objects_seen, "missing": missing, "tombstoned": tombstoned, "prefix": prefix})
        return {"missing": missing, "tombstoned": tombstoned}

    def upsert_catalogue_object(self, *, group_id: str, object_key: str, version_id: str = "",
                                size_bytes: int = 0, etag: str = "", checksum: str | None = None,
                                content_type: str = "application/octet-stream", source_mode: str = "DISCOVERED",
                                generation_id: str | None = None, origin_recon_id: str | None = None,
                                metadata: dict | None = None, storage_layout: str | None = None,
                                package_root: str | None = None, payload_key: str | None = None,
                                manifest_key: str | None = None) -> dict:
        group = self.get_catalogue_group(group_id)
        recon_id = self.recon_id_for(group, object_key)
        virtual_shard = self.virtual_shard_for(recon_id, group["virtual_shard_count"])
        shard = self.shard_for_virtual(group_id, virtual_shard)
        ts = now()
        existing = self.db.fetchone(
            "SELECT id,source_mode,storage_layout,package_root,payload_key,manifest_key FROM catalogue_objects WHERE catalogue_group_id=? AND object_key=?",
            (group_id, object_key),
        )
        if existing:
            oid = existing["id"]
            effective_source_mode = source_mode
            effective_layout = storage_layout or existing.get("storage_layout") or "DIRECT"
            effective_package_root = package_root if package_root is not None else existing.get("package_root")
            effective_payload_key = payload_key or existing.get("payload_key") or object_key
            effective_manifest_key = manifest_key if manifest_key is not None else existing.get("manifest_key")
            self.db.execute(
                "UPDATE catalogue_objects SET shard_id=?,recon_id=?,virtual_shard=?,version_id=?,logical_name=?,storage_layout=?,package_root=?,payload_key=?,manifest_key=?,content_type=?,size_bytes=?,etag=?,checksum_sha256=?,lifecycle_state='ACTIVE',missing_count=0,source_mode=?,origin_recon_id=COALESCE(?,origin_recon_id),last_seen_at=?,last_seen_generation_id=COALESCE(?,last_seen_generation_id),updated_at=?,tombstoned_at=NULL WHERE id=?",
                (shard["id"], recon_id, virtual_shard, version_id or "", object_key.rsplit("/", 1)[-1], effective_layout,
                 effective_package_root, effective_payload_key, effective_manifest_key, content_type, size_bytes, etag, checksum,
                 effective_source_mode, origin_recon_id, ts, generation_id, ts, oid),
            )
        else:
            oid = uid()
            effective_layout = storage_layout or "DIRECT"
            effective_payload_key = payload_key or object_key
            self.db.execute(
                "INSERT INTO catalogue_objects(id,catalogue_group_id,shard_id,recon_id,virtual_shard,object_key,version_id,logical_name,storage_layout,package_root,payload_key,manifest_key,content_type,size_bytes,etag,checksum_sha256,lifecycle_state,missing_count,source_mode,origin_recon_id,first_seen_at,last_seen_at,last_seen_generation_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, group_id, shard["id"], recon_id, virtual_shard, object_key, version_id or "", object_key.rsplit("/", 1)[-1],
                 effective_layout, package_root, effective_payload_key, manifest_key, content_type, size_bytes, etag, checksum,
                 "ACTIVE", 0, source_mode, origin_recon_id, ts, ts, generation_id, ts),
            )
            self.emit(group["tenant_id"], group_id, "CATALOGUE_OBJECT", oid, "CATALOGUE_OBJECT_REGISTERED",
                      {"recon_id": recon_id, "key": object_key, "native_version_id": version_id or "", "shard": shard["shard_no"],
                       "storage_layout": effective_layout, "payload_key": effective_payload_key})
        backend_response = (metadata or {}).get("backend_response") if metadata else {}
        self.record_payload_version(oid, group, object_key, version_id or "", etag, checksum, size_bytes, content_type, backend_response or {})
        for k, v in (metadata or {}).items():
            if k == "backend_response":
                continue
            self.upsert_metadata(oid, "STORAGE", "storage", k, v, True)
        return self.get_catalogue_object(oid)

    def record_payload_version(self, object_id: str, group: dict, object_key: str, native_version_id: str,
                               etag: str, checksum: str | None, size_bytes: int, content_type: str,
                               backend_response: dict | None = None, make_current: bool = True) -> None:
        if not native_version_id:
            self.db.execute("DELETE FROM catalogue_object_versions WHERE catalogue_object_id=?", (object_id,))
            return
        ts = now()
        if make_current:
            self.db.execute("UPDATE catalogue_object_versions SET is_current=? WHERE catalogue_object_id=?", (False, object_id))
        version_recon = str(uuid.uuid5(self._uuid_namespace(group["recon_namespace"]), f"{object_key}|{native_version_id}"))
        existing = self.db.fetchone("SELECT id FROM catalogue_object_versions WHERE catalogue_object_id=? AND native_version_id=?", (object_id, native_version_id))
        if existing:
            self.db.execute("UPDATE catalogue_object_versions SET version_recon_id=?,etag=?,checksum_sha256=?,size_bytes=?,content_type=?,is_current=?,observed_at=?,backend_response_json=? WHERE id=?",
                            (version_recon, etag, checksum, size_bytes, content_type, bool(make_current), ts, self.db.dumps(backend_response or {}), existing["id"]))
        else:
            self.db.execute("INSERT INTO catalogue_object_versions(id,catalogue_object_id,version_recon_id,native_version_id,etag,checksum_sha256,size_bytes,content_type,is_current,observed_at,backend_response_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (uid(), object_id, version_recon, native_version_id, etag, checksum, size_bytes, content_type, bool(make_current), ts, self.db.dumps(backend_response or {})))

    def list_payload_versions(self, object_id: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM catalogue_object_versions WHERE catalogue_object_id=? ORDER BY observed_at DESC", (object_id,))
        for r in rows:
            r["backend_response"] = self.db.loads(r.pop("backend_response_json", "{}"), {})
        return rows

    def record_annotation_version(self, annotation_id: str, native_version_id: str, payload_native_version_id: str, checksum: str, size_bytes: int,
                                  content_type: str, backend_response: dict | None = None) -> None:
        if not native_version_id:
            self.db.execute("DELETE FROM object_annotation_versions WHERE annotation_id=?", (annotation_id,))
            return
        ts = now()
        self.db.execute("UPDATE object_annotation_versions SET is_current=? WHERE annotation_id=?", (False, annotation_id))
        existing = self.db.fetchone("SELECT id FROM object_annotation_versions WHERE annotation_id=? AND native_version_id=?", (annotation_id, native_version_id))
        if existing:
            self.db.execute("UPDATE object_annotation_versions SET payload_native_version_id=?,checksum_sha256=?,size_bytes=?,content_type=?,is_current=?,observed_at=?,backend_response_json=? WHERE id=?",
                            (payload_native_version_id or "", checksum, size_bytes, content_type, True, ts, self.db.dumps(backend_response or {}), existing["id"]))
        else:
            self.db.execute("INSERT INTO object_annotation_versions(id,annotation_id,native_version_id,payload_native_version_id,checksum_sha256,size_bytes,content_type,is_current,observed_at,backend_response_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (uid(), annotation_id, native_version_id, payload_native_version_id or "", checksum, size_bytes, content_type, True, ts, self.db.dumps(backend_response or {})))

    def _link_annotation_snapshot(self, object_id: str, payload_native_version_id: str, annotation_name: str, annotation_native_version_id: str) -> None:
        if not payload_native_version_id:
            return
        existing = self.db.fetchone(
            "SELECT id FROM object_version_annotation_links WHERE catalogue_object_id=? AND payload_native_version_id=? AND annotation_name=?",
            (object_id, payload_native_version_id, annotation_name),
        )
        ts = now()
        if existing:
            self.db.execute(
                "UPDATE object_version_annotation_links SET annotation_native_version_id=?,linked_at=? WHERE id=?",
                (annotation_native_version_id or "", ts, existing["id"]),
            )
        else:
            self.db.execute(
                "INSERT INTO object_version_annotation_links(id,catalogue_object_id,payload_native_version_id,annotation_name,annotation_native_version_id,linked_at) VALUES(?,?,?,?,?,?)",
                (uid(), object_id, payload_native_version_id, annotation_name, annotation_native_version_id or "", ts),
            )

    def link_annotations_to_payload_version(self, object_id: str, payload_native_version_id: str) -> None:
        if not payload_native_version_id:
            return
        annotations = self.db.fetchall(
            "SELECT annotation_name,native_version_id FROM object_annotations WHERE catalogue_object_id=? AND state='ACTIVE'",
            (object_id,),
        )
        self.db.execute(
            "UPDATE object_annotations SET payload_native_version_id=?,updated_at=? WHERE catalogue_object_id=? AND state='ACTIVE'",
            (payload_native_version_id, now(), object_id),
        )
        for annotation in annotations:
            self._link_annotation_snapshot(
                object_id, payload_native_version_id, str(annotation["annotation_name"]), str(annotation.get("native_version_id") or "")
            )

    def annotation_version_for_payload(self, object_id: str, annotation_name: str, payload_native_version_id: str) -> str:
        if not payload_native_version_id:
            return ""
        row = self.db.fetchone(
            "SELECT annotation_native_version_id FROM object_version_annotation_links WHERE catalogue_object_id=? AND payload_native_version_id=? AND annotation_name=?",
            (object_id, payload_native_version_id, annotation_name),
        )
        return str((row or {}).get("annotation_native_version_id") or "")

    def list_version_annotation_links(self, object_id: str) -> list[dict]:
        return self.db.fetchall(
            "SELECT payload_native_version_id,annotation_name,annotation_native_version_id,linked_at FROM object_version_annotation_links WHERE catalogue_object_id=? ORDER BY linked_at DESC,annotation_name",
            (object_id,),
        )

    def list_annotation_versions(self, annotation_id: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM object_annotation_versions WHERE annotation_id=? ORDER BY observed_at DESC", (annotation_id,))
        for r in rows:
            r["backend_response"] = self.db.loads(r.pop("backend_response_json", "{}"), {})
        return rows

    @staticmethod
    def payload_key_for(obj: dict[str, Any]) -> str:
        return str(obj.get("payload_key") or obj.get("object_key") or "")

    def is_managed_physical_key(self, group_id: str, physical_key: str) -> bool:
        """Return True when a native storage key is an internal member of an AMP package.

        Match only physical members AMP explicitly owns. Broad package-root prefix
        matching can misclassify legitimate CLIENT_PATH customer keys and is not
        portable across PostgreSQL/SQLite.
        """
        row = self.db.fetchone(
            """SELECT id FROM catalogue_objects
               WHERE catalogue_group_id=?
                 AND storage_layout IN ('AMP_PACKAGE_V1','AMP_PACKAGE_V2','AMP_PACKAGE_V3')
                 AND (payload_key=? OR manifest_key=?)
               LIMIT 1""",
            (group_id, physical_key, physical_key),
        )
        if row:
            return True
        ann = self.db.fetchone(
            """SELECT a.id FROM object_annotations a
               JOIN catalogue_objects o ON o.id=a.catalogue_object_id
               WHERE o.catalogue_group_id=? AND a.sidecar_key=? LIMIT 1""",
            (group_id, physical_key),
        )
        return bool(ann)

    def upsert_metadata(self, object_id: str, source: str, namespace: str, key: str, value: Any, authoritative: bool = False):
        ts = now()
        existing = self.db.fetchone(
            "SELECT id FROM metadata_records WHERE catalogue_object_id=? AND source=? AND namespace=? AND key=?",
            (object_id, source, namespace, key),
        )
        value_text = value if isinstance(value, str) else None
        value_json = None if isinstance(value, str) else self.db.dumps(value)
        if existing:
            self.db.execute(
                "UPDATE metadata_records SET value_text=?,value_json=?,authoritative=?,updated_at=? WHERE id=?",
                (value_text, value_json, bool(authoritative), ts, existing["id"]),
            )
        else:
            self.db.execute(
                "INSERT INTO metadata_records(id,catalogue_object_id,source,namespace,key,value_text,value_json,authoritative,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (uid(), object_id, source, namespace, key, value_text, value_json, bool(authoritative), ts, ts),
            )

    def get_catalogue_object(self, oid: str) -> dict:
        obj = self.db.fetchone(
            """SELECT o.*,g.tenant_id,g.name catalogue_name,g.container_type,g.container_name,g.storage_id,
                      s.name storage_name,s.kind storage_kind,s.role storage_role
               FROM catalogue_objects o
               JOIN catalogue_groups g ON g.id=o.catalogue_group_id
               JOIN storage_systems s ON s.id=g.storage_id WHERE o.id=?""",
            (oid,),
        )
        if not obj:
            raise KeyError(oid)
        obj["shard"] = self.db.fetchone("SELECT shard_no,virtual_start,virtual_end,state FROM catalogue_shards WHERE id=?", (obj["shard_id"],))
        md = self.db.fetchall(
            "SELECT source,namespace,key,value_text,value_json,authoritative FROM metadata_records WHERE catalogue_object_id=? ORDER BY source,namespace,key",
            (oid,),
        )
        for m in md:
            m["value"] = self.db.loads(m["value_json"], m["value_json"]) if m.get("value_json") else m.get("value_text")
            m.pop("value_json", None); m.pop("value_text", None)
        obj["metadata"] = md
        obj["annotations"] = self.list_annotations(str(obj["id"]))
        for a in obj["annotations"]:
            if "backend_response_json" in a:
                a["backend_response"] = self.db.loads(a.pop("backend_response_json", "{}"), {})
            a["native_versions"] = self.list_annotation_versions(str(a["id"]))
        obj["native_versions"] = self.list_payload_versions(str(obj["id"]))
        obj["version_annotation_links"] = self.list_version_annotation_links(str(obj["id"]))
        return obj

    def list_catalogue_objects(self, *, tenant: str, group_id: str | None = None,
                               shard_id: str | None = None, q: str = "", prefix: str = "", limit: int = 200,
                               include_tombstones: bool = True) -> list[dict]:
        sql = """SELECT o.*,g.name catalogue_name,g.container_name,g.container_type,s.name storage_name
                 FROM catalogue_objects o JOIN catalogue_groups g ON g.id=o.catalogue_group_id
                 JOIN storage_systems s ON s.id=g.storage_id WHERE g.tenant_id=?"""
        params: list[Any] = [tenant]
        if group_id:
            sql += " AND o.catalogue_group_id=?"; params.append(group_id)
        if shard_id:
            sql += " AND o.shard_id=?"; params.append(shard_id)
        if not include_tombstones:
            sql += " AND o.lifecycle_state<>'TOMBSTONED'"
        if prefix:
            sql += " AND o.object_key LIKE ?"; params.append(f"{prefix}%")
        if q:
            sql += " AND (o.object_key LIKE ? OR CAST(o.recon_id AS TEXT) LIKE ?)"; params += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY o.updated_at DESC LIMIT ?"; params.append(limit)
        return self.db.fetchall(sql, params)

    def find_catalogue_object(self, group_id: str, object_key: str, version_id: str = "") -> dict | None:
        r = self.db.fetchone("SELECT id FROM catalogue_objects WHERE catalogue_group_id=? AND object_key=?", (group_id, object_key))
        return self.get_catalogue_object(r["id"]) if r else None

    def find_current_catalogue_object(self, group_id: str, object_key: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT id FROM catalogue_objects WHERE catalogue_group_id=? AND object_key=? AND lifecycle_state='ACTIVE' ORDER BY updated_at DESC LIMIT 1",
            (group_id, object_key),
        )
        return self.get_catalogue_object(row["id"]) if row else None

    def tombstone_by_key(self, group_id: str, object_key: str, version_id: str = "",
                         source_mode: str = "EVENT") -> dict:
        """Mark an object deleted from storage without removing its reconciliation identity.

        A versionless delete first targets the current versionless row. If a source only supplies
        key-level deletion events and no exact row exists, the newest active row for the key is used.
        """
        obj = self.find_catalogue_object(group_id, object_key, version_id or "")
        if not obj and not version_id:
            row = self.db.fetchone(
                "SELECT id FROM catalogue_objects WHERE catalogue_group_id=? AND object_key=? AND lifecycle_state<>'TOMBSTONED' ORDER BY updated_at DESC LIMIT 1",
                (group_id, object_key),
            )
            obj = self.get_catalogue_object(row["id"]) if row else None
        if not obj:
            # Preserve deletion knowledge even when the catalogue did not see the create. This gives
            # index/AI reconciliation a stable tombstone to compare against.
            obj = self.upsert_catalogue_object(
                group_id=group_id, object_key=object_key, version_id=version_id or "",
                size_bytes=0, etag="", checksum=None, content_type="application/octet-stream",
                source_mode=source_mode,
            )
        ts = now()
        self.db.execute(
            "UPDATE catalogue_objects SET lifecycle_state='TOMBSTONED',missing_count=2,source_mode=?,updated_at=?,last_seen_at=?,tombstoned_at=? WHERE id=?",
            (source_mode, ts, ts, ts, obj["id"]),
        )
        return self.get_catalogue_object(obj["id"])

    def resolve_catalogue_group(self, storage_id: str, container_type: str, container_name: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT id FROM catalogue_groups WHERE storage_id=? AND container_type=? AND container_name=?",
            (storage_id, container_type.upper(), container_name),
        )
        return self.get_catalogue_group(row["id"]) if row else None
