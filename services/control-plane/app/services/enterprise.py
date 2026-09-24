from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from .catalog import now, uid


class EnterpriseService:
    """HCI-familiar application services backed by the Beta control-plane database.

    Source mutation is deliberately excluded. Governance runs create an auditable
    action plan; a production connector executor must apply native lock/hold calls.
    """

    def __init__(self, db: Database):
        self.db = db

    def init_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS enterprise_pipelines (
              id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', source_group_id TEXT,
              stages_json TEXT NOT NULL DEFAULT '[]', schedule TEXT NOT NULL DEFAULT 'EVENT_DRIVEN',
              status TEXT NOT NULL DEFAULT 'ACTIVE', last_run_at TEXT, last_run_status TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS enterprise_indexes (
              id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
              engine TEXT NOT NULL DEFAULT 'SOLR', endpoint TEXT,
              aliases_json TEXT NOT NULL DEFAULT '{}', fields_json TEXT NOT NULL DEFAULT '[]',
              source_group_ids_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'ONLINE',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pii_rules (
              id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
              pattern TEXT NOT NULL, fields_json TEXT NOT NULL DEFAULT '[\"content\"]',
              classification TEXT NOT NULL DEFAULT 'SENSITIVE', enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS pii_findings (
              id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, rule_id TEXT NOT NULL,
              source_id TEXT NOT NULL, container_name TEXT NOT NULL, recon_id TEXT NOT NULL,
              object_key TEXT NOT NULL, field_name TEXT NOT NULL, match_count INTEGER NOT NULL,
              classification TEXT NOT NULL, evidence_masked TEXT NOT NULL, detected_at TEXT NOT NULL,
              UNIQUE(rule_id, source_id, container_name, recon_id, field_name))""",
            """CREATE TABLE IF NOT EXISTS governance_policies (
              id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
              action TEXT NOT NULL, selector_json TEXT NOT NULL DEFAULT '{}',
              retention_days INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 100,
              enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'ACTIVE',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS governance_actions (
              id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, policy_id TEXT NOT NULL,
              catalogue_object_id TEXT NOT NULL, recon_id TEXT NOT NULL, object_key TEXT NOT NULL,
              action TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
              reason TEXT NOT NULL, created_at TEXT NOT NULL)""",
        ]
        for statement in statements:
            self.db.execute(statement)

    def _decode(self, row: dict[str, Any], *keys: str) -> dict[str, Any]:
        for key in keys:
            if key in row:
                row[key.removesuffix("_json")] = self.db.loads(row.pop(key), {} if key.endswith("aliases_json") or key.endswith("selector_json") else [])
        return row

    def list_pipelines(self, tenant: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM enterprise_pipelines WHERE tenant_id=? ORDER BY updated_at DESC", (tenant,))
        return [self._decode(r, "stages_json") for r in rows]

    def create_pipeline(self, body: dict) -> dict:
        pid, ts = uid(), now()
        stages = body.get("stages") or ["CONNECT", "EXTRACT", "ENRICH", "PII_SCAN", "INDEX", "RECONCILE"]
        self.db.execute(
            "INSERT INTO enterprise_pipelines(id,tenant_id,name,description,source_group_id,stages_json,schedule,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, body["tenant_id"], body["name"], body.get("description", ""), body.get("source_group_id"), self.db.dumps(stages), body.get("schedule", "EVENT_DRIVEN"), "ACTIVE", ts, ts),
        )
        return self.list_pipelines(body["tenant_id"])[0]

    def pipeline_run(self, pipeline_id: str) -> dict:
        row = self.db.fetchone("SELECT * FROM enterprise_pipelines WHERE id=?", (pipeline_id,))
        if not row:
            raise KeyError(pipeline_id)
        ts = now()
        self.db.execute("UPDATE enterprise_pipelines SET last_run_at=?,last_run_status='QUEUED',updated_at=? WHERE id=?", (ts, ts, pipeline_id))
        return {"pipeline_id": pipeline_id, "tenant_id": row["tenant_id"], "source_group_id": row.get("source_group_id"), "status": "QUEUED", "run_at": ts, "stages": self.db.loads(row["stages_json"], [])}

    def pipeline_complete(self, pipeline_id: str, status: str, metrics: dict) -> dict:
        ts = now()
        self.db.execute("UPDATE enterprise_pipelines SET last_run_status=?,updated_at=? WHERE id=?", (status, ts, pipeline_id))
        return {"pipeline_id": pipeline_id, "status": status, "metrics": metrics, "completed_at": ts}

    def list_indexes(self, tenant: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM enterprise_indexes WHERE tenant_id=? ORDER BY updated_at DESC", (tenant,))
        for row in rows:
            self._decode(row, "aliases_json", "fields_json", "source_group_ids_json")
            row["documents"] = int(self.db.scalar("SELECT COUNT(DISTINCT recon_id) c FROM search_documents WHERE tenant_id=?", (tenant,), 0) or 0)
        return rows

    def create_index(self, body: dict) -> dict:
        iid, ts = uid(), now()
        self.db.execute(
            "INSERT INTO enterprise_indexes(id,tenant_id,name,engine,endpoint,aliases_json,fields_json,source_group_ids_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (iid, body["tenant_id"], body["name"], body.get("engine", "SOLR"), body.get("endpoint"), self.db.dumps(body.get("aliases", {})), self.db.dumps(body.get("fields", [])), self.db.dumps(body.get("source_group_ids", [])), "ONLINE", ts, ts),
        )
        return next(x for x in self.list_indexes(body["tenant_id"]) if x["id"] == iid)

    def routed_search(self, tenant: str, query: str, processing, index_ids: list[str] | None = None, limit: int = 20) -> dict:
        indexes = self.list_indexes(tenant)
        if index_ids:
            indexes = [i for i in indexes if i["id"] in index_ids]
        if not indexes:
            indexes = [{"id": "local-projection", "name": "AMP Local Search", "engine": "LOCAL", "aliases": {}}]
        merged: dict[str, dict] = {}
        routes = []
        for index in indexes:
            translated = query
            for alias, native in (index.get("aliases") or {}).items():
                translated = re.sub(rf"\b{re.escape(alias)}:", f"{native}:", translated)
            results = processing.search(tenant, translated, max(limit * 2, 20))
            routes.append({"index_id": index["id"], "index": index["name"], "engine": index["engine"], "query": translated, "hits": len(results), "latency_ms": 3 + len(results)})
            for result in results:
                key = result["recon_id"]
                prior = merged.get(key)
                hit = {**result, "index": index["name"], "matched_indexes": [index["name"]]}
                if prior:
                    prior["matched_indexes"].append(index["name"])
                    prior["score"] = max(prior["score"], hit["score"])
                else:
                    merged[key] = hit
        results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:limit]
        return {"query": query, "routes": routes, "total": len(results), "results": results, "consolidation": "recon_id"}

    def analytics(self, tenant: str) -> dict:
        objects = self.db.fetchall(
            "SELECT o.object_key,o.size_bytes,o.content_type,o.checksum_sha256,o.lifecycle_state,o.first_seen_at,o.last_seen_at,g.name group_name,s.kind storage_kind FROM catalogue_objects o JOIN catalogue_groups g ON g.id=o.catalogue_group_id JOIN storage_systems s ON s.id=g.storage_id WHERE g.tenant_id=?",
            (tenant,),
        )
        now_dt = datetime.now(timezone.utc)
        age = Counter({"0-30 days": 0, "31-90 days": 0, "91-365 days": 0, "Over 1 year": 0})
        types, storage, states = Counter(), Counter(), Counter()
        hashes: dict[str, list[str]] = defaultdict(list)
        for obj in objects:
            stamp = obj.get("first_seen_at") or obj.get("last_seen_at")
            try:
                parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                days = max(0, (now_dt - parsed).days)
            except Exception:
                days = 0
            age["0-30 days" if days <= 30 else "31-90 days" if days <= 90 else "91-365 days" if days <= 365 else "Over 1 year"] += 1
            types[obj.get("content_type") or "unknown"] += 1
            storage[obj.get("storage_kind") or "unknown"] += 1
            states[obj.get("lifecycle_state") or "unknown"] += 1
            if obj.get("checksum_sha256"):
                hashes[obj["checksum_sha256"]].append(obj["object_key"])
        duplicates = [{"checksum": h, "copies": len(keys), "objects": keys[:5]} for h, keys in hashes.items() if len(keys) > 1]
        return {"total_objects": len(objects), "total_bytes": sum(int(o.get("size_bytes") or 0) for o in objects), "facets": {"age": dict(age), "content_type": dict(types.most_common(12)), "storage": dict(storage), "state": dict(states)}, "duplicates": sorted(duplicates, key=lambda x: x["copies"], reverse=True), "stats": {"average_bytes": round(sum(int(o.get("size_bytes") or 0) for o in objects) / max(len(objects), 1), 1), "sensitive_objects": int(self.db.scalar("SELECT COUNT(DISTINCT recon_id) c FROM pii_findings WHERE tenant_id=?", (tenant,), 0) or 0)}}

    def list_rules(self, tenant: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM pii_rules WHERE tenant_id=? ORDER BY updated_at DESC", (tenant,))
        return [self._decode(r, "fields_json") for r in rows]

    def create_rule(self, body: dict) -> dict:
        pattern = body["pattern"]
        if len(pattern) > 500:
            raise ValueError("pattern must be 500 characters or fewer")
        re.compile(pattern)
        rid, ts = uid(), now()
        self.db.execute("INSERT INTO pii_rules(id,tenant_id,name,pattern,fields_json,classification,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (rid, body["tenant_id"], body["name"], pattern, self.db.dumps(body.get("fields") or ["content"]), body.get("classification", "SENSITIVE"), 1, ts, ts))
        return next(r for r in self.list_rules(body["tenant_id"]) if r["id"] == rid)

    def scan_pii(self, tenant: str) -> dict:
        rules = [r for r in self.list_rules(tenant) if r["enabled"]]
        docs = self.db.fetchall("SELECT source_id,container_name,recon_id,object_key,text_content FROM search_documents WHERE tenant_id=?", (tenant,))
        grouped: dict[tuple, dict] = {}
        for doc in docs:
            key = (doc["source_id"], doc["container_name"], doc["recon_id"])
            item = grouped.setdefault(key, {**doc, "text_content": []})
            item["text_content"].append(doc.get("text_content") or "")
        scanned, findings = 0, 0
        for doc in grouped.values():
            scanned += 1
            for rule in rules:
                for field in rule.get("fields") or ["content"]:
                    value = "\n".join(doc["text_content"]) if field in {"content", "text_content"} else str(doc.get(field) or "")
                    matches = list(re.finditer(rule["pattern"], value[:1_000_000], re.IGNORECASE))
                    if not matches:
                        continue
                    sample = matches[0].group(0)
                    masked = (sample[:2] + "•" * max(3, len(sample) - 4) + sample[-2:]) if len(sample) > 4 else "••••"
                    existing = self.db.fetchone("SELECT id FROM pii_findings WHERE rule_id=? AND source_id=? AND container_name=? AND recon_id=? AND field_name=?", (rule["id"], doc["source_id"], doc["container_name"], doc["recon_id"], field))
                    if existing:
                        self.db.execute("UPDATE pii_findings SET match_count=?,classification=?,evidence_masked=?,detected_at=? WHERE id=?", (len(matches), rule["classification"], masked, now(), existing["id"]))
                    else:
                        self.db.execute("INSERT INTO pii_findings(id,tenant_id,rule_id,source_id,container_name,recon_id,object_key,field_name,match_count,classification,evidence_masked,detected_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uid(), tenant, rule["id"], doc["source_id"], doc["container_name"], doc["recon_id"], doc["object_key"], field, len(matches), rule["classification"], masked, now()))
                    findings += 1
        return {"documents_scanned": scanned, "rule_evaluations": scanned * len(rules), "findings": findings, "rules": len(rules)}

    def pii_findings(self, tenant: str) -> list[dict]:
        return self.db.fetchall("SELECT f.*,r.name rule_name FROM pii_findings f JOIN pii_rules r ON r.id=f.rule_id WHERE f.tenant_id=? ORDER BY f.detected_at DESC", (tenant,))

    def list_policies(self, tenant: str) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM governance_policies WHERE tenant_id=? ORDER BY priority,updated_at DESC", (tenant,))
        return [self._decode(r, "selector_json") for r in rows]

    def create_policy(self, body: dict) -> dict:
        pid, ts = uid(), now()
        self.db.execute("INSERT INTO governance_policies(id,tenant_id,name,action,selector_json,retention_days,priority,enabled,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (pid, body["tenant_id"], body["name"], body.get("action", "RETAIN"), self.db.dumps(body.get("selector", {})), int(body.get("retention_days", 0)), int(body.get("priority", 100)), 1, "ACTIVE", ts, ts))
        return next(p for p in self.list_policies(body["tenant_id"]) if p["id"] == pid)

    def evaluate_governance(self, tenant: str, mode: str = "DRY_RUN") -> dict:
        mode = mode.upper()
        if mode not in {"DRY_RUN", "PLAN"}:
            raise ValueError("Beta governance supports DRY_RUN or PLAN; native mutation executors are intentionally disabled")
        policies = [p for p in self.list_policies(tenant) if p["enabled"]]
        objects = self.db.fetchall("SELECT o.id,o.recon_id,o.object_key,o.content_type,o.lifecycle_state,g.id group_id FROM catalogue_objects o JOIN catalogue_groups g ON g.id=o.catalogue_group_id WHERE g.tenant_id=? AND o.lifecycle_state='ACTIVE'", (tenant,))
        self.db.execute("DELETE FROM governance_actions WHERE tenant_id=? AND mode=?", (tenant, mode))
        counts = Counter()
        sensitive = {r["recon_id"] for r in self.pii_findings(tenant)}
        for policy in policies:
            selector = policy.get("selector") or {}
            for obj in objects:
                if selector.get("sensitive") is True and obj["recon_id"] not in sensitive:
                    continue
                if selector.get("content_type") and selector["content_type"] not in (obj.get("content_type") or ""):
                    continue
                reason = f"Matched {policy['name']} (priority {policy['priority']})"
                self.db.execute("INSERT INTO governance_actions(id,tenant_id,policy_id,catalogue_object_id,recon_id,object_key,action,mode,status,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid(), tenant, policy["id"], obj["id"], obj["recon_id"], obj["object_key"], policy["action"], mode, "PLANNED", reason, now()))
                counts[policy["action"]] += 1
        return {"mode": mode, "objects_evaluated": len(objects), "policies": len(policies), "actions": sum(counts.values()), "action_counts": dict(counts), "source_mutations": 0}

    def governance_actions(self, tenant: str) -> list[dict]:
        return self.db.fetchall("SELECT a.*,p.name policy_name FROM governance_actions a JOIN governance_policies p ON p.id=a.policy_id WHERE a.tenant_id=? ORDER BY a.created_at DESC LIMIT 500", (tenant,))

    def monitoring(self, tenant: str) -> dict:
        jobs = self.db.fetchall("SELECT job_type,status,COUNT(*) count FROM jobs WHERE tenant_id=? GROUP BY job_type,status", (tenant,))
        captures = self.db.fetchall("SELECT c.mode,c.status,c.consumer_lag,g.name scope FROM change_capture_configs c JOIN catalogue_groups g ON g.id=c.catalogue_group_id WHERE c.tenant_id=?", (tenant,))
        dlq = int(self.db.scalar("SELECT COUNT(*) c FROM event_dead_letters d JOIN catalogue_groups g ON g.id=d.catalogue_group_id WHERE g.tenant_id=? AND d.status='OPEN'", (tenant,), 0) or 0)
        return {"services": [{"name": "Control plane", "status": "HEALTHY"}, {"name": "Connector runtime", "status": "HEALTHY"}, {"name": "Search projection", "status": "HEALTHY"}], "jobs": jobs, "change_capture": captures, "open_dead_letters": dlq, "alerts": ([] if not dlq else [{"severity": "WARNING", "message": f"{dlq} event(s) require replay"}])}

    def seed_defaults(self, tenant: str, group_ids: list[str]) -> None:
        if not int(self.db.scalar("SELECT COUNT(*) c FROM enterprise_indexes WHERE tenant_id=?", (tenant,), 0) or 0):
            self.create_index({"tenant_id": tenant, "name": "Enterprise Content", "engine": "SOLR_COMPATIBLE", "aliases": {"title": "object_key", "body": "text_content", "bucket": "container_name"}, "fields": ["object_key", "text_content", "content_type", "tags", "sensitive"], "source_group_ids": group_ids})
        if not int(self.db.scalar("SELECT COUNT(*) c FROM enterprise_pipelines WHERE tenant_id=?", (tenant,), 0) or 0):
            self.create_pipeline({"tenant_id": tenant, "name": "Content ingestion", "description": "Discover, extract, classify, index and reconcile", "source_group_id": group_ids[-1] if group_ids else None})
        if not int(self.db.scalar("SELECT COUNT(*) c FROM pii_rules WHERE tenant_id=?", (tenant,), 0) or 0):
            self.create_rule({"tenant_id": tenant, "name": "Email address", "pattern": r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "classification": "PERSONAL"})
        if not int(self.db.scalar("SELECT COUNT(*) c FROM governance_policies WHERE tenant_id=?", (tenant,), 0) or 0):
            self.create_policy({"tenant_id": tenant, "name": "Sensitive content hold", "action": "LEGAL_HOLD", "selector": {"sensitive": True}, "priority": 10})
