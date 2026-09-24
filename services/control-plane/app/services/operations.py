from __future__ import annotations
from typing import Any

from .catalog import CatalogService, now, uid
from .storage import backend_from_record
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
            for item in native_items:
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

    def _storage_inventory(self, group: dict, backend, prefix: str = "") -> tuple[dict[str, dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
        """Build a logical inventory from authoritative storage.

        This generic implementation is intentionally reserved for explicit TALLY/FULL
        integrity work. Production HCP/AWS implementations can replace the physical
        listing source with MQE/S3 Inventory without changing the reconciliation model.
        """
        native_items = list(backend.list(prefix))
        logical: dict[str, dict[str, Any]] = {}
        for item in native_items:
            key = str(item.key)
            logical[key] = {
                "kind": "DIRECT",
                "layout": "DIRECT",
                "payload_key": key,
                "manifest_key": None,
                "package_root": None,
            }

        return logical, {
            "native_items": len(native_items),
            "logical_objects": len(logical),
        }, []

    @staticmethod
    def _finding_counts(findings: list[tuple[dict, str, str, str, dict]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for _obj, _target, finding, _severity, _details in findings:
            out[finding] = out.get(finding, 0) + 1
        return dict(sorted(out.items()))

    def reconcile(self, tenant: str, group_id: str, target: str = "ALL", shard_id: str | None = None,
                  *, prefix: str = "", storage_mode: str = "TARGETED",
                  verify_package_members: bool = True, limit: int = 10000) -> dict:
        group = self.catalog.get_catalogue_group(group_id)
        if group["tenant_id"] != tenant:
            raise ValueError("catalogue group does not belong to tenant")
        target = target.upper()
        if target not in {"ALL", "STORAGE", "INDEX", "AI"}:
            raise ValueError("target must be ALL, STORAGE, INDEX or AI")
        storage_mode = str(storage_mode or "TARGETED").upper()
        if storage_mode not in {"TARGETED", "TALLY", "FULL"}:
            raise ValueError("storage_mode must be TARGETED, TALLY or FULL")
        limit = max(1, min(int(limit), 1_000_000))

        selector = {
            "target": target,
            "prefix": prefix,
            "storage_mode": storage_mode,
            "verify_package_members": bool(verify_package_members),
            "limit": limit,
        }
        jid = self._job(tenant, f"{target}_RECONCILIATION", group_id=group_id, shard_id=shard_id,
                        selector=selector)
        findings: list[tuple[dict, str, str, str, dict]] = []
        checked = {"storage": 0, "index": 0, "ai": 0}
        rows = self.catalog.list_catalogue_objects(
            tenant=tenant, group_id=group_id, shard_id=shard_id, prefix=prefix,
            limit=limit, include_tombstones=True,
        )
        active = [r for r in rows if r["lifecycle_state"] in {"ACTIVE", "MISSING"}]
        active_by_key = {str(r["object_key"]): r for r in active}
        all_by_key = {str(r["object_key"]): r for r in rows}

        count_sql = """SELECT COUNT(*) c FROM catalogue_objects o
                       JOIN catalogue_groups g ON g.id=o.catalogue_group_id
                       WHERE g.tenant_id=? AND o.catalogue_group_id=?"""
        count_params: list[Any] = [tenant, group_id]
        if shard_id:
            count_sql += " AND o.shard_id=?"; count_params.append(shard_id)
        if prefix:
            count_sql += " AND o.object_key LIKE ?"; count_params.append(f"{prefix}%")
        scope_total = int(self.db.scalar(count_sql, count_params, 0) or 0)
        active_sql = count_sql + " AND o.lifecycle_state IN ('ACTIVE','MISSING')"
        scope_active_total = int(self.db.scalar(active_sql, count_params, 0) or 0)
        scope_truncated = scope_total > len(rows)

        inventory_metrics: dict[str, Any] = {}
        inventory_errors: list[dict[str, Any]] = []

        try:
            if storage_mode == "FULL" and scope_active_total > limit:
                raise ValueError(
                    f"FULL reconciliation scope contains {scope_active_total} active objects, exceeding limit {limit}; "
                    "narrow the physical shard/prefix or raise the explicit limit"
                )
            if target in {"ALL", "STORAGE"}:
                backend = backend_from_record(self.catalog.backend_record_for_group(group_id))

                # TARGETED verifies bounded Catalogue rows with point HEADs and is the
                # normal frequent reconciliation path.
                if storage_mode in {"TARGETED", "FULL"}:
                    for obj in active:
                        checked["storage"] += 1
                        payload_key = self.catalog.payload_key_for(obj)
                        try:
                            stat = backend.head(payload_key)
                            if not stat:
                                findings.append((obj, "STORAGE", "MISSING_FROM_STORAGE", "HIGH",
                                                 {"key": obj["object_key"], "payload_key": payload_key}))
                                continue

                            if int(stat.size) != int(obj.get("size_bytes") or 0):
                                findings.append((obj, "STORAGE", "SIZE_MISMATCH", "MEDIUM",
                                                 {"catalogue": obj.get("size_bytes"), "storage": stat.size,
                                                  "payload_key": payload_key}))
                            if obj.get("checksum_sha256") and stat.checksum_sha256 and obj["checksum_sha256"] != stat.checksum_sha256:
                                findings.append((obj, "STORAGE", "CHECKSUM_MISMATCH", "HIGH",
                                                 {"catalogue": obj["checksum_sha256"], "storage": stat.checksum_sha256,
                                                  "payload_key": payload_key}))
                            if obj.get("version_id") and stat.version_id and str(obj["version_id"]) != str(stat.version_id):
                                findings.append((obj, "STORAGE", "VERSION_DRIFT", "MEDIUM",
                                                 {"catalogue": obj.get("version_id"), "storage": stat.version_id,
                                                  "payload_key": payload_key}))

                        except Exception as exc:
                            findings.append((obj, "STORAGE", "VERIFY_FAILED", "MEDIUM",
                                             {"payload_key": payload_key, "error": str(exc)}))

                # TALLY/FULL performs an explicit authoritative inventory. It is not
                # intended as the daily billion-object path; real adapters can source
                # this inventory from HCP MQE or S3 Inventory.
                if storage_mode in {"TALLY", "FULL"}:
                    inventory, inventory_metrics, inventory_errors = self._storage_inventory(group, backend, prefix)
                    inventory_keys = set(inventory)
                    catalogue_keys = set(active_by_key)
                    inventory_metrics.update({
                        "catalogue_active": scope_active_total,
                        "count_delta": len(inventory_keys) - scope_active_total,
                        "strategy": "GENERIC_BACKEND_LIST",
                    })
                    if len(inventory_keys) != scope_active_total:
                        pseudo = {"id": None, "recon_id": None, "shard_id": shard_id}
                        findings.append((pseudo, "STORAGE", "COUNT_MISMATCH", "MEDIUM",
                                         {"catalogue_active": scope_active_total,
                                          "storage_logical": len(inventory_keys),
                                          "delta": len(inventory_keys) - scope_active_total}))

                    if storage_mode == "FULL":
                        for logical_key in sorted(inventory_keys):
                            recon_id = self.catalog.recon_id_for(group, logical_key)
                            virtual = self.catalog.virtual_shard_for(recon_id, group["virtual_shard_count"])
                            physical = self.catalog.shard_for_virtual(group_id, virtual)
                            if shard_id and str(physical["id"]) != str(shard_id):
                                continue
                            current = all_by_key.get(logical_key)
                            if current and current.get("lifecycle_state") == "TOMBSTONED":
                                findings.append((current, "STORAGE", "TOMBSTONED_BUT_PRESENT", "HIGH",
                                                 {"object_key": logical_key,
                                                  "inventory_kind": inventory[logical_key]["kind"]}))
                            elif logical_key not in active_by_key:
                                pseudo = {"id": None, "recon_id": recon_id, "shard_id": physical["id"]}
                                findings.append((pseudo, "STORAGE", "EXTRA_IN_STORAGE", "HIGH",
                                                 {"object_key": logical_key,
                                                  "inventory_kind": inventory[logical_key]["kind"],
                                                  "payload_key": inventory[logical_key]["payload_key"]}))

                    for err in inventory_errors[:100]:
                        pseudo = {"id": None, "recon_id": None, "shard_id": shard_id}
                        findings.append((pseudo, "STORAGE", "INVENTORY_READ_ERROR", "MEDIUM", err))

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
                        findings.append((obj, "INDEX", "STALE_INDEX", "MEDIUM",
                                         {"catalogue_hash": obj["checksum_sha256"], "index_hash": idx["content_hash"]}))
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
                        findings.append((obj, "AI", "STALE_AI_ARTIFACT", "MEDIUM",
                                         {"catalogue_hash": obj["checksum_sha256"], "ai_hash": ai["source_content_hash"]}))

            for obj, target_type, finding, severity, details in findings:
                self.db.execute(
                    "INSERT INTO reconciliation_results(id,job_id,catalogue_group_id,shard_id,catalogue_object_id,recon_id,target_type,finding_type,severity,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (uid(), jid, group_id, obj.get("shard_id") or shard_id, obj.get("id"), obj.get("recon_id"),
                     target_type, finding, severity, self.db.dumps(details), now()),
                )
            metrics = {
                "checked": checked,
                "findings": len(findings),
                "finding_counts": self._finding_counts(findings),
                "target": target,
                "catalogue_objects": len(rows),
                "active_objects": len(active),
                "scope_total": scope_total,
                "scope_active_total": scope_active_total,
                "scope_truncated": scope_truncated,
                "prefix": prefix,
                "storage_mode": storage_mode,
                "inventory": inventory_metrics,
                "inventory_errors": len(inventory_errors),
                "verify_package_members": bool(verify_package_members),
            }
            status = "COMPLETE" if not inventory_errors else "COMPLETE_WITH_WARNINGS"
            self._finish(jid, status, metrics)
            return {"job_id": jid, **metrics}
        except Exception as exc:
            self._finish(jid, "FAILED", {
                "checked": checked,
                "findings": len(findings),
                "target": target,
                "prefix": prefix,
                "storage_mode": storage_mode,
            }, str(exc))
            raise
