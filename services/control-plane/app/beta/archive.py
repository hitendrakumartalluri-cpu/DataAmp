from __future__ import annotations
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ..services.catalog import now, uid
from .destinations import Destination, digest


class Conflict(ValueError):
    pass


class ArchiveService:
    """Durable staging -> verified archive -> independently retryable indexing."""
    def __init__(self, db, catalog, processing, root):
        self.db, self.catalog, self.processing = db, catalog, processing
        self.root = Path(root).resolve() / "archive-staging"
        self.root.mkdir(parents=True, exist_ok=True)
        self.workbench = None

    def init_schema(self):
        for statement in [
            """CREATE TABLE IF NOT EXISTS archive_profiles(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
              name TEXT NOT NULL,version INTEGER NOT NULL,config_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS archive_items(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
              profile_id TEXT NOT NULL,profile_json TEXT NOT NULL,idempotency_key TEXT NOT NULL,
              fingerprint TEXT NOT NULL,batch_id TEXT NOT NULL,business_id TEXT NOT NULL,revision TEXT NOT NULL,
              name TEXT NOT NULL,content_type TEXT NOT NULL,bytes BIGINT NOT NULL,sha256 TEXT NOT NULL,
              metadata_json TEXT NOT NULL,staged_path TEXT NOT NULL,payload_key TEXT NOT NULL,
              metadata_key TEXT NOT NULL,archive_status TEXT NOT NULL,index_status TEXT NOT NULL,
              receipt_json TEXT NOT NULL DEFAULT '{}',error TEXT,attempts INTEGER NOT NULL DEFAULT 0,
              lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              UNIQUE(tenant_id,profile_id,idempotency_key))""",
            """CREATE TABLE IF NOT EXISTS archive_attempts(id TEXT PRIMARY KEY,item_id TEXT NOT NULL,
              stage TEXT NOT NULL,status TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS archive_outbox(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
              item_id TEXT NOT NULL,event_type TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
              UNIQUE(item_id,event_type))""",
        ]:
            self.db.execute(statement)

    def create_profile(self, tenant, name, config):
        config = json.loads(json.dumps(config))
        group = self.catalog.get_catalogue_group(config["destination_group_id"])
        if group["tenant_id"] != tenant:
            raise PermissionError("destination belongs to a different tenant")
        if config.get("cleanup", "COPY_ONLY") != "COPY_ONLY":
            raise ValueError("source cleanup requires a qualified lifecycle executor; beta is COPY_ONLY")
        if config.get("source", {}).get("kind", "API") not in {"API", "MOUNT", "SFTP", "OBJECT"}:
            raise ValueError("source requires a qualified connector")
        config["destination_snapshot"] = self.catalog.backend_record_for_group(config["destination_group_id"])
        for field, rule in config.get("mapping", {}).items():
            if not isinstance(rule, dict) or rule.get("type", "string") not in {"string", "integer", "date", "boolean"}:
                raise ValueError("mapping rules require a supported type")
        pid = uid()
        self.db.execute("INSERT INTO archive_profiles(id,tenant_id,name,version,config_json,created_at) VALUES(?,?,?,?,?,?)",
            (pid, tenant, name, 1, self.db.dumps(config), now()))
        return self.profile(tenant, pid)

    def profile(self, tenant, pid):
        row = self.db.fetchone("SELECT * FROM archive_profiles WHERE id=? AND tenant_id=?", (pid, tenant))
        if not row:
            raise KeyError(pid)
        row["config"] = self.db.loads(row.pop("config_json"), {})
        return row

    def profiles(self, tenant):
        return [self.profile(tenant, x["id"]) for x in self.db.fetchall("SELECT id FROM archive_profiles WHERE tenant_id=? ORDER BY created_at DESC", (tenant,))]

    def enrich(self, metadata, profile):
        result = dict(metadata)
        config = profile["config"]
        for target, rule in config.get("mapping", {}).items():
            value = rule.get("constant") if "constant" in rule else metadata.get(rule.get("source", target))
            if value is None or value == "":
                if rule.get("required"):
                    raise ValueError(f"required metadata missing: {target}")
                continue
            kind = rule.get("type", "string")
            if kind == "integer":
                value = int(value)
            elif kind == "boolean":
                if value not in (True, False, "true", "false"):
                    raise ValueError(f"invalid boolean: {target}")
                value = value in (True, "true")
            elif kind == "date":
                parsed = datetime.strptime(str(value), rule["format"]) if rule.get("format") else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    if not rule.get("timezone"):
                        raise ValueError(f"date timezone required: {target}")
                    from zoneinfo import ZoneInfo
                    parsed = parsed.replace(tzinfo=ZoneInfo(rule["timezone"]))
                value = parsed.astimezone(timezone.utc).isoformat()
            else:
                value = str(value)
            result[target] = value
        return result

    def submit(self, tenant, pid, path, name, business_id, revision, metadata, idempotency_key, batch_id="", expected_sha256=None):
        if not business_id or not revision or not idempotency_key:
            raise ValueError("business_id, revision and idempotency_key are required")
        profile = self.profile(tenant, pid)
        path = Path(path)
        item_id = uid()
        stage = self.root / item_id
        before = path.stat()
        with path.open("rb") as source, stage.open("xb") as out:
            shutil.copyfileobj(source, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        directory_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        after = path.stat()
        source_changed = (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        checksum = digest(stage)
        metadata = json.loads(json.dumps(metadata))
        fingerprint = hashlib.sha256(self.db.dumps({"name": name, "business_id": business_id,
            "revision": revision, "sha256": checksum, "metadata": metadata, "expected_sha256": expected_sha256}).encode()).hexdigest()
        existing = self.db.fetchone("SELECT id,fingerprint FROM archive_items WHERE tenant_id=? AND profile_id=? AND idempotency_key=?", (tenant, pid, idempotency_key))
        if existing:
            stage.unlink()
            if existing["fingerprint"] != fingerprint:
                raise Conflict("idempotency key was already used with different content or metadata")
            return self.item(tenant, existing["id"])
        status, error = "ACCEPTED", None
        try:
            if source_changed or (expected_sha256 and expected_sha256 != checksum):
                raise ValueError("SOURCE_CHANGED_OR_CHECKSUM_MISMATCH")
            enriched = self.enrich(metadata, profile)
            backend = Destination(profile["config"]["destination_snapshot"])
            backend.validate_policy(profile["config"].get("protection", {}))
        except ValueError as exc:
            enriched, status, error = metadata, "QUARANTINED", str(exc)
        enriched.update(archive_id=item_id, business_id=business_id, revision=revision,
            source_name=name, profile_id=pid, mapping_version=profile["version"], content_sha256=checksum)
        prefix = f"amp-archive/{hashlib.sha256(tenant.encode()).hexdigest()[:16]}/{item_id}"
        try:
            self.db.execute("""INSERT INTO archive_items(id,tenant_id,profile_id,profile_json,idempotency_key,
                fingerprint,batch_id,business_id,revision,name,content_type,bytes,sha256,metadata_json,staged_path,
                payload_key,metadata_key,archive_status,index_status,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, tenant, pid, self.db.dumps(profile), idempotency_key, fingerprint, batch_id, business_id,
                 revision, name, mimetypes.guess_type(name)[0] or "application/octet-stream", stage.stat().st_size,
                 checksum, self.db.dumps(enriched), str(stage), prefix + "/payload", prefix + "/metadata.json",
                 status, "PENDING", error, now(), now()))
        except Exception:
            stage.unlink(missing_ok=True)
            raced = self.db.fetchone("SELECT id,fingerprint FROM archive_items WHERE tenant_id=? AND profile_id=? AND idempotency_key=?", (tenant, pid, idempotency_key))
            if raced and raced["fingerprint"] == fingerprint:
                return self.item(tenant, raced["id"])
            raise
        self.catalog.audit(tenant, "ARCHIVE_SUBMIT", details={"archive_id": item_id, "status": status})
        return self.item(tenant, item_id)

    def item(self, tenant, iid):
        row = self.db.fetchone("SELECT * FROM archive_items WHERE id=? AND tenant_id=?", (iid, tenant))
        if not row:
            raise KeyError(iid)
        for field in ("metadata", "receipt", "profile"):
            row[field] = self.db.loads(row.pop(field + "_json"), {})
        row.pop("staged_path")
        # Profile source paths/enrichment details stay operator-only; receipts are public API data.
        row.pop("profile")
        return row

    def items(self, tenant):
        return [self.item(tenant, r["id"]) for r in self.db.fetchall("SELECT id FROM archive_items WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1000", (tenant,))]

    def _artifact(self, destination, key, staged, expected, policy, content_type="application/octet-stream"):
        stat = destination.head(key)
        if not stat:
            stat = destination.write(key, staged, content_type, policy)
        with tempfile.NamedTemporaryFile(dir=self.root) as verify:
            actual = destination.read_to(key, stat["version"], verify.name)
        if actual != expected:
            raise Conflict("destination checksum does not match submitted revision")
        evidence = destination.verify_policy(key, stat["version"], policy)
        return {"key": key, "version": stat["version"], "sha256": actual, "bytes": stat["bytes"], "protection": evidence}

    def process(self, tenant, iid):
        self.item(tenant, iid)
        # Atomic lease supports multiple workers/processes; one attempt holds the item.
        stamp = time.time()
        with self.db.connection() as conn:
            cur = conn.execute(self.db._sql("UPDATE archive_items SET lease_until=?,attempts=attempts+1 WHERE id=? AND tenant_id=? AND lease_until<?"), (stamp + 3600, iid, tenant, stamp))
            if cur.rowcount != 1:
                raise Conflict("archive item is already being processed")
        row = self.db.fetchone("SELECT * FROM archive_items WHERE id=? AND tenant_id=?", (iid, tenant))
        if row["archive_status"] == "QUARANTINED":
            self.db.execute("UPDATE archive_items SET lease_until=0 WHERE id=?", (iid,))
            return self.item(tenant, iid)
        try:
            profile = self.db.loads(row["profile_json"], {})
            config = profile["config"]
            dest = Destination(config["destination_snapshot"])
            if row["archive_status"] != "ARCHIVED":
                stage = Path(row["staged_path"])
                if not stage.is_file() or digest(stage) != row["sha256"]:
                    raise ValueError("durable staging integrity check failed")
                payload = self._artifact(dest, row["payload_key"], stage, row["sha256"], config.get("protection", {}), row["content_type"])
                envelope = {"schema_version": 1, "tenant": tenant, "archive_id": iid,
                    "metadata": self.db.loads(row["metadata_json"], {}), "payload": payload,
                    "profile_id": row["profile_id"], "profile_version": profile["version"]}
                envelope_path = self.root / (iid + ".metadata")
                serialized = self.db.dumps(envelope).encode()
                envelope_path.write_bytes(serialized)
                meta = self._artifact(dest, row["metadata_key"], envelope_path, hashlib.sha256(serialized).hexdigest(), config.get("protection", {}), "application/json")
                receipt = {"schema_version": 1, "archive_id": iid, "tenant": tenant,
                    "business_id": row["business_id"], "revision": row["revision"], "payload": payload,
                    "metadata": meta, "destination_group_id": config["destination_group_id"],
                    "accepted_at": row["created_at"], "verified_at": now(), "source_cleanup": "COPY_ONLY"}
                with self.db.connection() as conn:
                    conn.execute(self.db._sql("UPDATE archive_items SET archive_status='ARCHIVED',receipt_json=?,error=NULL,updated_at=? WHERE id=?"), (self.db.dumps(receipt), now(), iid))
                    conn.execute(self.db._sql("INSERT INTO archive_outbox VALUES(?,?,?,?,?,?) ON CONFLICT(item_id,event_type) DO NOTHING"), (uid(), tenant, iid, "ARCHIVE_VERIFIED", "PENDING", now()))
                self.catalog.audit(tenant, "ARCHIVE_VERIFIED", recon_id=iid, details={"receipt": receipt})
            if row["index_status"] != "SEARCHABLE":
                self.index_item(tenant, iid)
            self._attempt(iid, "ARCHIVE", "COMPLETE", {})
        except Exception as exc:
            latest = self.item(tenant, iid)
            state = latest["archive_status"] if latest["archive_status"] == "ARCHIVED" else "RETRY_PENDING"
            self.db.execute("UPDATE archive_items SET archive_status=?,error=?,updated_at=? WHERE id=?", (state, str(exc), now(), iid))
            self._attempt(iid, "ARCHIVE" if state != "ARCHIVED" else "INDEX", "FAILED", {"error": str(exc)})
        finally:
            self.db.execute("UPDATE archive_items SET lease_until=0 WHERE id=?", (iid,))
        return self.item(tenant, iid)

    def _attempt(self, iid, stage, status, details):
        self.db.execute("INSERT INTO archive_attempts VALUES(?,?,?,?,?,?)", (uid(), iid, stage, status, self.db.dumps(details), now()))

    def index_item(self, tenant, iid):
        item = self.item(tenant, iid)
        if item["archive_status"] != "ARCHIVED":
            raise ValueError("archive verification is required before indexing")
        receipt = item["receipt"]
        dest = self.destination_for(tenant, iid)
        with tempfile.NamedTemporaryFile(dir=self.root) as restored:
            checksum = dest.read_to(receipt["payload"]["key"], receipt["payload"]["version"], restored.name)
            if checksum != item["sha256"]:
                raise ValueError("archive changed before indexing")
            with open(restored.name, "rb") as inp:
                data = inp.read(int(os.getenv("AMP_MAX_EXTRACT_BYTES", str(8 * 1024 * 1024))))
        text, extraction = self.processing.extract_text(data, item["content_type"], item["name"])
        source = item["profile_id"]
        with self.db.connection() as conn:
            conn.execute(self.db._sql("DELETE FROM search_documents WHERE tenant_id=? AND recon_id=? AND source_id=?"), (tenant, iid, source))
            # Even metadata-only/empty content produces a searchable name projection.
            chunks = [text[n:n + 1800] for n in range(0, len(text), 1800)] or [item["name"]]
            for seq, chunk in enumerate(chunks):
                conn.execute(self.db._sql("""INSERT INTO search_documents(id,tenant_id,source_id,container_type,
                    container_name,recon_id,source_version,object_key,content_hash,chunk_seq,text_content,text_hash,
                    embedding_json,pipeline_version,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""),
                    (uid(), tenant, source, "ARCHIVE", item["profile_id"], iid, item["revision"], item["name"],
                     item["sha256"], seq, chunk, hashlib.sha256(chunk.encode()).hexdigest(), "[]", "archive-v1", now()))

        if self.workbench:
            fields = {**item["metadata"], "object_key": item["name"], "content_type": item["content_type"],
                "size_bytes": item["bytes"], "source_id": source, "archive_id": iid, "indexed_at": now(), "extraction": extraction}
            self.workbench.project(tenant, source, iid, fields)
        with self.db.connection() as conn:
            conn.execute(self.db._sql("UPDATE archive_items SET index_status='SEARCHABLE',error=NULL,updated_at=? WHERE id=?"), (now(), iid))
            conn.execute(self.db._sql("UPDATE archive_outbox SET status='PROCESSED' WHERE item_id=?"), (iid,))
        if self.workbench:
            self.workbench.ledger.record(tenant, source, iid, item["revision"], item["sha256"], "LOCAL_SEARCH_VISIBLE")
        self._attempt(iid, "INDEX", "SEARCHABLE", extraction)

    def destination_for(self, tenant, iid):
        row = self.db.fetchone("SELECT profile_json FROM archive_items WHERE id=? AND tenant_id=?", (iid, tenant))
        if not row:
            raise KeyError(iid)
        return Destination(self.db.loads(row["profile_json"], {})["config"]["destination_snapshot"])

    def download(self, tenant, iid, target):
        item = self.item(tenant, iid)
        if item["archive_status"] != "ARCHIVED":
            raise ValueError("archive is not verified")
        dest = self.destination_for(tenant, iid)
        artifact = item["receipt"]["payload"]
        if dest.read_to(artifact["key"], artifact["version"], target) != artifact["sha256"]:
            raise ValueError("retrieval checksum verification failed")
        self.catalog.audit(tenant, "ARCHIVE_RETRIEVE", recon_id=iid, details={"archive_id": iid})
        return item

    def reconcile(self, tenant):
        findings = []
        for item in self.items(tenant):
            if item["archive_status"] != "ARCHIVED":
                continue
            try:
                dest = self.destination_for(tenant, item["id"])
                for field in ("payload", "metadata"):
                    part = item["receipt"][field]
                    with tempfile.NamedTemporaryFile(dir=self.root) as temp:
                        if dest.read_to(part["key"], part["version"], temp.name) != part["sha256"]:
                            findings.append({"archive_id": item["id"], "type": field.upper() + "_CHECKSUM_MISMATCH"})
                    profile = self.db.fetchone("SELECT profile_json FROM archive_items WHERE id=?", (item["id"],))
                    protection = self.db.loads(profile["profile_json"], {})["config"].get("protection", {})
                    dest.verify_policy(part["key"], part["version"], protection)
                if not self.db.scalar("SELECT COUNT(*) FROM search_documents WHERE tenant_id=? AND recon_id=?", (tenant, item["id"]), 0):
                    findings.append({"archive_id": item["id"], "type": "MISSING_FROM_INDEX"})
            except Exception as exc:
                findings.append({"archive_id": item["id"], "type": "VERIFY_FAILED", "error": str(exc)})
        return {"findings": findings, "count": len(findings)}

    def collect_mount(self, tenant, pid, manifest):
        profile = self.profile(tenant, pid)
        source = profile["config"].get("source", {})
        if source.get("kind") != "MOUNT":
            raise ValueError("profile is not a mounted source")
        root = Path(source["root"]).resolve()
        manifest_path = (root / manifest).resolve()
        if root not in manifest_path.parents:
            raise ValueError("manifest escapes source root")
        marker = manifest_path.with_suffix(manifest_path.suffix + ".ready")
        if not marker.is_file():
            raise ValueError("producer ready marker is required")
        from .packages import normalize_manifest
        body = normalize_manifest(manifest_path, "mount-batch")
        if body.get("schema_version") != 1 or len(body.get("items", [])) > 10000:
            raise ValueError("invalid or oversized manifest")
        results = []
        for item in body["items"]:
            try:
                path = (root / item["path"]).resolve()
                if root not in path.parents or not path.is_file():
                    raise ValueError("invalid source file")
                if path.stat().st_size > int(os.getenv("AMP_MAX_UPLOAD_BYTES", str(1024 ** 3))):
                    raise ValueError("source file exceeds configured limit")
                submitted = self.submit(tenant, pid, path, item["path"], item["business_id"], str(item["revision"]), item.get("metadata", {}), item["idempotency_key"], body["batch_id"], expected_sha256=item.get("sha256"))
                results.append(self.item(tenant, submitted["id"]))
            except Exception as exc:
                results.append({"name": item.get("path"), "archive_status": "FAILED", "error": str(exc)})
        return {"batch_id": body["batch_id"], "items": results}

    def collect(self, tenant, pid, request):
        from .collectors import collect
        return collect(self, tenant, pid, request)

    def tick(self, limit=20):
        rows = self.db.fetchall("SELECT id,tenant_id FROM archive_items WHERE (archive_status IN ('ACCEPTED','RETRY_PENDING') OR (archive_status='ARCHIVED' AND index_status='PENDING')) AND lease_until<? AND attempts<5 ORDER BY created_at LIMIT ?", (time.time(), limit))
        for row in rows:
            self.process(row["tenant_id"], row["id"])
        return len(rows)
