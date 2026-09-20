from __future__ import annotations
import json
from typing import Any

from .catalog import CatalogService, now, uid
from .storage import backend_from_record
from .managed_objects import ManagedObjectService
from ..db import Database


class OperationsService:
    def __init__(self, db: Database, catalog: CatalogService):
        self.db = db
        self.catalog = catalog

    def _job(self, tenant: str, typ: str, *, group_id: str | None = None, shard_id: str | None = None,
             source_group: str | None = None, target_group: str | None = None, selector: dict | None = None) -> str:
        jid, ts = uid(), now()
        self.db.execute(
            """INSERT INTO jobs(id,tenant_id,job_type,status,progress,catalogue_group_id,shard_id,source_group_id,target_group_id,selector_json,metrics_json,created_at,started_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (jid, tenant, typ, "RUNNING", 0, group_id, shard_id, source_group, target_group,
             self.db.dumps(selector or {}), self.db.dumps({}), ts, ts),
        )
        return jid

    def _finish(self, jid: str, status: str, metrics: dict, error: str | None = None):
        progress = 100 if status in {"COMPLETE", "COMPLETE_WITH_WARNINGS"} else 0
        self.db.execute(
            "UPDATE jobs SET status=?,progress=?,metrics_json=?,error_message=?,completed_at=? WHERE id=?",
            (status, progress, self.db.dumps(metrics), error, now(), jid),
        )

    def discover(self, tenant: str, group_id: str, prefix: str = "") -> dict:
        group = self.catalog.get_catalogue_group(group_id)
        if group["tenant_id"] != tenant:
            raise ValueError("catalogue group does not belong to tenant")
        if group["state"] != "ACTIVE":
            raise ValueError(f"catalogue group is {group['state']}; discovery requires ACTIVE")
        jid = self._job(tenant, "CATALOGUE_DISCOVERY", group_id=group_id,
                        selector={"prefix": prefix, "container": group["container_name"]})
        gen = self.catalog.begin_generation(group_id, jid, prefix)
        scanned = registered = updated = 0
        errors: list[dict[str, Any]] = []
        try:
            backend = backend_from_record(self.catalog.backend_record_for_group(group_id))
            native_items = list(backend.list(prefix))
            if prefix and not prefix.startswith(".amp/"):
                # Hash-managed V2/V3 package roots live under the reserved AMP prefix
                # and cannot be located from a logical client prefix alone. Scan the
                # manifest namespace in addition to the requested logical range.
                seen = {x.key for x in native_items}
                native_items.extend(x for x in backend.list(".amp/objects/") if x.key not in seen)
            package_roots: set[str] = set()

            # AMP package manifests make managed objects reconstructable from storage.
            # V1 manifests live beside the logical key; V2 packages live entirely
            # under the reserved .amp/objects/<package-id>/ namespace.
            for item in native_items:
                is_manifest_candidate = item.key.endswith("/manifest.json") or item.key.endswith("/.amp/manifest.json")
                if not is_manifest_candidate:
                    continue
                try:
                    manifest = json.loads(backend.get(item.key).decode("utf-8"))
                    layout = str(manifest.get("storageLayout") or "")
                    if layout not in {"AMP_PACKAGE_V1", "AMP_PACKAGE_V2", "AMP_PACKAGE_V3"}:
                        continue
                    logical_key = str(manifest.get("logicalPath") or "").strip("/")
                    if prefix and not logical_key.startswith(prefix):
                        continue
                    package_root = str(manifest.get("packageRoot") or logical_key).strip("/")
                    payload_key = str((manifest.get("payload") or {}).get("key") or (f"{package_root}/payload" if package_root else ""))
                    if not logical_key or not payload_key:
                        raise ValueError("package manifest missing logicalPath/payload")
                    package_roots.add(package_root)
                    stat = backend.head(payload_key)
                    if not stat:
                        # Ignore provisional WRITING manifests until payload exists.
                        if str(manifest.get("state") or "").upper() == "WRITING":
                            continue
                        raise FileNotFoundError(f"package payload missing: {payload_key}")
                    before = self.catalog.find_catalogue_object(group_id, logical_key, stat.version_id or "")
                    self.catalog.upsert_catalogue_object(
                        group_id=group_id, object_key=logical_key, version_id=stat.version_id or "",
                        size_bytes=stat.size, etag=stat.etag, checksum=stat.checksum_sha256,
                        content_type=str((manifest.get("payload") or {}).get("contentType") or stat.content_type),
                        source_mode="DISCOVERED", generation_id=gen["id"], storage_layout=layout,
                        package_root=package_root, payload_key=payload_key, manifest_key=item.key,
                        metadata={"reconstructed_from_manifest": True},
                    )
                    scanned += 1
                    updated += 1 if before else 0
                    registered += 0 if before else 1
                except Exception as exc:
                    errors.append({"key": item.key, "error": str(exc)})

            for item in native_items:
                if item.key.startswith(".amp/"):
                    continue
                if item.key.endswith("/.amp/manifest.json"):
                    continue
                if any(item.key == f"{root}/payload" or item.key == f"{root}/manifest.json"
                       or item.key.startswith(f"{root}/annotations/") or item.key.startswith(f"{root}/.amp/")
                       for root in package_roots):
                    continue
                scanned += 1
                try:
                    before = self.catalog.find_catalogue_object(group_id, item.key, item.version_id or "")
                    self.catalog.upsert_catalogue_object(
                        group_id=group_id, object_key=item.key, version_id=item.version_id or "",
                        size_bytes=item.size, etag=item.etag, checksum=item.checksum_sha256,
                        content_type=item.content_type, source_mode="DISCOVERED", generation_id=gen["id"],
                    )
                    updated += 1 if before else 0
                    registered += 0 if before else 1
                except Exception as exc:
                    errors.append({"key": item.key, "error": str(exc)})
            lifecycle = self.catalog.finish_generation(group_id, gen["id"], scanned, prefix)
            metrics = {
                "generation": gen["generation_no"], "scanned": scanned, "native_items": len(native_items),
                "registered": registered, "updated": updated, "missing": lifecycle["missing"],
                "tombstoned": lifecycle["tombstoned"], "errors": len(errors),
            }
            self._finish(jid, "COMPLETE" if not errors else "COMPLETE_WITH_WARNINGS", metrics)
            return {"job_id": jid, "generation_id": gen["id"], **metrics, "details": errors[:20]}
        except Exception as exc:
            self._finish(jid, "FAILED", {"scanned": scanned, "registered": registered}, str(exc))
            self.db.execute("UPDATE catalogue_generations SET status='FAILED',completed_at=? WHERE id=?", (now(), gen["id"]))
            raise

    def migrate(self, tenant: str, source_group_id: str, target_group_id: str,
                prefix: str = "", dry_run: bool = False) -> dict:
        """Migrate current logical objects between independent catalogue groups.

        AMP copies current payload + managed annotations and lets the target backend create
        its own native versions/compliance state. Historic-version migration is deliberately
        separate; AMP never creates synthetic v1/v2 object keys or owns pruning/lifecycle.
        """
        src_group = self.catalog.get_catalogue_group(source_group_id)
        tgt_group = self.catalog.get_catalogue_group(target_group_id)
        if src_group["tenant_id"] != tenant or tgt_group["tenant_id"] != tenant:
            raise ValueError("source/target catalogue group does not belong to tenant")
        if target_group_id == source_group_id:
            raise ValueError("source and target catalogue groups must differ")
        jid = self._job(tenant, "MIGRATION", source_group=source_group_id, target_group=target_group_id,
                        selector={"prefix": prefix, "dry_run": dry_run, "version_scope": "CURRENT"})
        src_backend = backend_from_record(self.catalog.backend_record_for_group(source_group_id))
        tgt_backend = backend_from_record(self.catalog.backend_record_for_group(target_group_id))
        managed = ManagedObjectService(self.db, self.catalog)
        scanned = copied = skipped = 0
        bytes_copied = 0
        errors: list[dict[str, Any]] = []
        try:
            rows = self.catalog.list_catalogue_objects(tenant=tenant, group_id=source_group_id,
                                                       limit=10_000_000, include_tombstones=False)
            rows = [r for r in rows if r.get("lifecycle_state") == "ACTIVE" and (not prefix or str(r.get("object_key") or "").startswith(prefix))]
            for row in rows:
                scanned += 1
                logical_key = str(row["object_key"])
                try:
                    source_obj = self.catalog.get_catalogue_object(str(row["id"]))
                    source_payload_key = self.catalog.payload_key_for(source_obj)
                    src_stat = src_backend.head(source_payload_key)
                    if not src_stat:
                        raise FileNotFoundError(source_payload_key)

                    target_obj = self.catalog.find_current_catalogue_object(target_group_id, logical_key)
                    target_stat = None
                    if target_obj:
                        target_stat = tgt_backend.head(self.catalog.payload_key_for(target_obj))
                    else:
                        _, target_payload_key, _ = managed.package_keys(tgt_group, logical_key)
                        target_stat = tgt_backend.head(target_payload_key)

                    same = False
                    if target_stat:
                        if source_obj.get("checksum_sha256") and target_stat.checksum_sha256:
                            same = str(source_obj.get("checksum_sha256")) == str(target_stat.checksum_sha256)
                        else:
                            same = int(target_stat.size) == int(src_stat.size)
                    if same:
                        skipped += 1
                        if target_obj and not dry_run:
                            self._link_migration(jid, source_group_id, source_obj["recon_id"],
                                                 target_group_id, target_obj["recon_id"])
                        continue

                    if dry_run:
                        copied += 1
                        bytes_copied += int(src_stat.size)
                        continue

                    data = src_backend.get(source_payload_key)
                    annotations: list[dict[str, Any]] = []
                    for ann in source_obj.get("annotations") or []:
                        if str(ann.get("state") or "ACTIVE").upper() != "ACTIVE":
                            continue
                        try:
                            annotations.append({
                                "name": ann["annotation_name"],
                                "content_type": ann.get("content_type") or "application/octet-stream",
                                "data": src_backend.get(str(ann["sidecar_key"])),
                            })
                        except Exception as exc:
                            raise RuntimeError(f"annotation {ann.get('annotation_name')} read failed: {exc}") from exc

                    target_obj = managed.put_to_group(
                        target_group_id, logical_key, data, source_obj.get("content_type") or src_stat.content_type,
                        source_mode="MIGRATED", origin_recon_id=str(source_obj["recon_id"]), annotations=annotations,
                    )
                    self._link_migration(jid, source_group_id, source_obj["recon_id"],
                                         target_group_id, target_obj["recon_id"])
                    copied += 1
                    bytes_copied += len(data)
                except Exception as exc:
                    errors.append({"key": logical_key, "error": str(exc)})
            metrics = {"scanned": scanned, "copied": copied, "skipped": skipped,
                       "bytes": bytes_copied, "errors": len(errors), "dry_run": dry_run,
                       "version_scope": "CURRENT", "compliance_authority": "BACKEND"}
            self._finish(jid, "COMPLETE" if not errors else "COMPLETE_WITH_WARNINGS", metrics)
            return {"job_id": jid, **metrics, "details": errors[:20]}
        except Exception as exc:
            self._finish(jid, "FAILED", {"scanned": scanned, "copied": copied}, str(exc))
            raise

    def _link_migration(self, job_id: str, source_group: str, source_recon: str,
                        target_group: str, target_recon: str) -> None:
        exists = self.db.fetchone(
            "SELECT id FROM migration_links WHERE source_group_id=? AND source_recon_id=? AND target_group_id=? AND target_recon_id=?",
            (source_group, source_recon, target_group, target_recon),
        )
        if not exists:
            self.db.execute(
                "INSERT INTO migration_links(id,job_id,source_group_id,source_recon_id,target_group_id,target_recon_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (uid(), job_id, source_group, source_recon, target_group, target_recon, now()),
            )

    def reconcile(self, tenant: str, group_id: str, target: str = "ALL", shard_id: str | None = None) -> dict:
        group = self.catalog.get_catalogue_group(group_id)
        if group["tenant_id"] != tenant:
            raise ValueError("catalogue group does not belong to tenant")
        target = target.upper()
        if target not in {"ALL", "STORAGE", "INDEX", "AI"}:
            raise ValueError("target must be ALL, STORAGE, INDEX or AI")
        jid = self._job(tenant, f"{target}_RECONCILIATION", group_id=group_id, shard_id=shard_id,
                        selector={"target": target})
        findings: list[tuple[dict, str, str, str, dict]] = []
        checked = {"storage": 0, "index": 0, "ai": 0}
        rows = self.catalog.list_catalogue_objects(tenant=tenant, group_id=group_id, shard_id=shard_id,
                                                   limit=10_000_000, include_tombstones=True)
        active = [r for r in rows if r["lifecycle_state"] in {"ACTIVE", "MISSING"}]

        if target in {"ALL", "STORAGE"}:
            backend = backend_from_record(self.catalog.backend_record_for_group(group_id))
            for obj in active:
                checked["storage"] += 1
                try:
                    stat = backend.head(self.catalog.payload_key_for(obj))
                    if not stat:
                        findings.append((obj, "STORAGE", "MISSING_FROM_STORAGE", "HIGH", {"key": obj["object_key"], "payload_key": self.catalog.payload_key_for(obj)}))
                    elif int(stat.size) != int(obj.get("size_bytes") or 0):
                        findings.append((obj, "STORAGE", "SIZE_MISMATCH", "MEDIUM", {"catalogue": obj.get("size_bytes"), "storage": stat.size}))
                    elif obj.get("checksum_sha256") and stat.checksum_sha256 and obj["checksum_sha256"] != stat.checksum_sha256:
                        findings.append((obj, "STORAGE", "CHECKSUM_MISMATCH", "HIGH", {"catalogue": obj["checksum_sha256"], "storage": stat.checksum_sha256}))
                except Exception as exc:
                    findings.append((obj, "STORAGE", "VERIFY_FAILED", "MEDIUM", {"error": str(exc)}))

        if target in {"ALL", "INDEX"}:
            for obj in active:
                checked["index"] += 1
                idx = self.db.fetchone(
                    "SELECT content_hash,pipeline_version,indexed_at FROM search_documents WHERE source_id=? AND container_type=? AND container_name=? AND recon_id=? LIMIT 1",
                    (group["storage_id"], group["container_type"], group["container_name"], obj["recon_id"]),
                )
                if not idx:
                    findings.append((obj, "INDEX", "MISSING_FROM_INDEX", "MEDIUM", {"recon_id": obj["recon_id"]}))
                elif obj.get("checksum_sha256") and idx.get("content_hash") and obj["checksum_sha256"] != idx["content_hash"]:
                    findings.append((obj, "INDEX", "STALE_INDEX", "MEDIUM", {"catalogue_hash": obj["checksum_sha256"], "index_hash": idx["content_hash"]}))
            # Index orphans: search docs that no longer have an active catalogue row.
            indexed_ids = self.db.fetchall(
                "SELECT DISTINCT recon_id FROM search_documents WHERE source_id=? AND container_type=? AND container_name=?",
                (group["storage_id"], group["container_type"], group["container_name"]),
            )
            known = {r["recon_id"] for r in active}
            for r in indexed_ids:
                if r["recon_id"] not in known:
                    pseudo = {"id": None, "recon_id": r["recon_id"], "shard_id": shard_id}
                    findings.append((pseudo, "INDEX", "EXTRA_IN_INDEX", "LOW", {"recon_id": r["recon_id"]}))

        if target in {"ALL", "AI"}:
            for obj in active:
                checked["ai"] += 1
                ai = self.db.fetchone(
                    "SELECT source_content_hash,model_name,model_version,pipeline_version,status FROM ai_artifacts WHERE source_id=? AND container_type=? AND container_name=? AND recon_id=? ORDER BY updated_at DESC LIMIT 1",
                    (group["storage_id"], group["container_type"], group["container_name"], obj["recon_id"]),
                )
                if not ai:
                    findings.append((obj, "AI", "MISSING_AI_ARTIFACT", "LOW", {"recon_id": obj["recon_id"]}))
                elif obj.get("checksum_sha256") and ai.get("source_content_hash") and obj["checksum_sha256"] != ai["source_content_hash"]:
                    findings.append((obj, "AI", "STALE_AI_ARTIFACT", "MEDIUM", {"catalogue_hash": obj["checksum_sha256"], "ai_hash": ai["source_content_hash"]}))

        for obj, target_type, finding, severity, details in findings:
            self.db.execute(
                "INSERT INTO reconciliation_results(id,job_id,catalogue_group_id,shard_id,catalogue_object_id,recon_id,target_type,finding_type,severity,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (uid(), jid, group_id, obj.get("shard_id") or shard_id, obj.get("id"), obj.get("recon_id"),
                 target_type, finding, severity, self.db.dumps(details), now()),
            )
        metrics = {"checked": checked, "findings": len(findings), "target": target,
                   "catalogue_objects": len(rows), "active_objects": len(active)}
        self._finish(jid, "COMPLETE", metrics)
        return {"job_id": jid, **metrics}
