from __future__ import annotations
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..services.catalog import now, uid
from .security import Identity, current_identity


class WorkbenchService:
    """Issue-backed beta workflows. Local projections are labelled, never sold as Solr."""
    def __init__(self, db, catalog, processing, root):
        self.db, self.catalog, self.processing = db, catalog, processing
        self.root = Path(root).resolve() / "workbench"
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive = None
        from .index_ledger import IndexLedger
        self.ledger = IndexLedger(db)

    def init_schema(self):
        self.ledger.init_schema()
        for sql in [
            """CREATE TABLE IF NOT EXISTS document_projection(tenant_id TEXT NOT NULL,source_id TEXT NOT NULL,
                recon_id TEXT NOT NULL,fields_json TEXT NOT NULL,schema_version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,PRIMARY KEY(tenant_id,source_id,recon_id))""",
            """CREATE TABLE IF NOT EXISTS access_maps(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
                principal TEXT NOT NULL,effect TEXT NOT NULL,selector_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS beta_jobs(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,actor TEXT NOT NULL,
                kind TEXT NOT NULL,status TEXT NOT NULL,request_json TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}',
                expires_at DOUBLE PRECISION NOT NULL DEFAULT 0,token_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS beta_schemas(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,version INTEGER NOT NULL,fields_json TEXT NOT NULL,created_at TEXT NOT NULL,
                UNIQUE(tenant_id,name,version))""",
            """CREATE TABLE IF NOT EXISTS beta_dashboards(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,config_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS beta_classifiers(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,selector_json TEXT NOT NULL,tags_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
        ]:
            self.db.execute(sql)

    def project(self, tenant, source, recon, fields):
        self.db.execute("""INSERT INTO document_projection(tenant_id,source_id,recon_id,fields_json,schema_version,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,source_id,recon_id) DO UPDATE SET fields_json=excluded.fields_json,
            schema_version=excluded.schema_version,updated_at=excluded.updated_at""", (tenant, source, recon, self.db.dumps(fields), 1, now()))

    @staticmethod
    def matches(fields, selector):
        return all(fields.get(key) in value if isinstance(value, list) else fields.get(key) == value for key, value in selector.items())

    def add_access(self, tenant, principal, effect, selector):
        if effect not in {"GRANT", "DENY"} or not selector:
            raise ValueError("nonempty selector and GRANT/DENY effect required")
        rid = uid()
        self.db.execute("INSERT INTO access_maps VALUES(?,?,?,?,?,?)", (rid, tenant, principal, effect, self.db.dumps(selector), now()))
        return {"id": rid}

    def allowed(self, identity, fields):
        if identity.admin:
            return True
        principals = {identity.actor, "*", *("group:" + group for group in identity.groups)}
        grant = False
        for rule in self.db.fetchall("SELECT * FROM access_maps WHERE tenant_id=?", (identity.tenant,)):
            if rule["principal"] in principals and self.matches(fields, self.db.loads(rule["selector_json"], {})):
                if rule["effect"] == "DENY":
                    return False
                grant = True
        return grant

    def documents(self, identity):
        if identity is None:
            raise PermissionError("identity required")
        rows = self.db.fetchall("SELECT * FROM document_projection WHERE tenant_id=? ORDER BY recon_id", (identity.tenant,))
        result = []
        for row in rows:
            fields = self.db.loads(row["fields_json"], {})
            if self.allowed(identity, fields):
                result.append({"source_id": row["source_id"], "recon_id": row["recon_id"], "fields": fields,
                    "schema_version": row["schema_version"]})
        return result

    def archive_access_fields(self, item):
        row = self.db.fetchone("SELECT fields_json FROM document_projection WHERE tenant_id=? AND source_id=? AND recon_id=?", (item["tenant_id"], item["profile_id"], item["id"]))
        return self.db.loads(row["fields_json"], {}) if row else item["metadata"]

    def authorized_ids(self, identity):
        return {(doc["source_id"], doc["recon_id"]) for doc in self.documents(identity)}

    def bootstrap_projections(self, tenant):
        for doc in self.db.fetchall("SELECT DISTINCT source_id,recon_id,object_key,content_hash,indexed_at FROM search_documents WHERE tenant_id=?", (tenant,)):
            if not self.db.fetchone("SELECT recon_id FROM document_projection WHERE tenant_id=? AND source_id=? AND recon_id=?", (tenant, doc["source_id"], doc["recon_id"])):
                self.project(tenant, doc["source_id"], doc["recon_id"], {**doc, "sensitivity": "UNMAPPED"})

    def search(self, identity, query="", filters=None, limit=100, index_ids=None):
        if len(query) > 2000:
            raise ValueError("query too long")
        filters = filters or []
        known = {key for doc in self.documents(identity) for key in doc["fields"]}
        for filt in filters:
            if filt.get("op", "eq") not in {"eq", "in", "gte", "lte"} or filt.get("field") not in known:
                raise ValueError("unknown filter field or unsupported operator")
        docs = self.documents(identity)
        indexes = self.db.fetchall("SELECT * FROM enterprise_indexes WHERE tenant_id=?", (identity.tenant,))
        if index_ids:
            indexes = [index for index in indexes if index["id"] in index_ids]
            if len(indexes) != len(set(index_ids)):
                raise ValueError("unknown or unauthorized index")
            scopes = {g for index in indexes for g in self.db.loads(index["source_group_ids_json"], [])}
            if scopes:
                storage_ids = {self.catalog.get_catalogue_group(g)["storage_id"] for g in scopes}
                docs = [doc for doc in docs if doc["source_id"] in storage_ids]
        candidates = []
        for doc in docs:
            fields = doc["fields"]
            match = True
            for filt in filters:
                value, wanted = fields.get(filt["field"]), filt.get("value")
                op = filt.get("op", "eq")
                try:
                    valid = value == wanted if op == "eq" else value in wanted if op == "in" else value >= wanted if op == "gte" else value <= wanted
                except TypeError:
                    valid = False
                match = match and valid
            if match:
                candidates.append(doc)
        terms = re.findall(r'"([^"]+)"|([^\s]+)', query.lower())
        tokens = [a or b for a, b in terms]
        results = []
        for doc in candidates:
            # Access and metadata filters are applied before fetching/scoring text.
            chunks = self.db.fetchall("SELECT text_content,source_version FROM search_documents WHERE tenant_id=? AND source_id=? AND recon_id=? ORDER BY chunk_seq", (identity.tenant, doc["source_id"], doc["recon_id"]))
            text = "\n".join(x["text_content"] for x in chunks)
            combined = self.db.dumps(doc["fields"]).lower() + " " + text.lower()
            if not all(token in combined for token in tokens):
                continue
            score = sum(combined.count(token) for token in tokens) or 1
            results.append({**doc, "name": doc["fields"].get("object_key", doc["recon_id"]),
                "object_key": doc["fields"].get("object_key", ""), "score": score, "snippet": text[:420],
                "source_version": chunks[0]["source_version"] if chunks else ""})
        results.sort(key=lambda x: (-x["score"], x["source_id"], x["recon_id"]))
        self.catalog.audit(identity.tenant, "SEARCH", actor=identity.actor, details={"query_sha256": hashlib.sha256(query.encode()).hexdigest(), "hits": len(results)})
        return {"results": results[:max(1, min(limit, 10000))], "total": len(results),
            "engine": "LOCAL_BETA", "routes": [{"index_id": index["id"], "name": index["name"]} for index in indexes],
            "consolidation": "source_id+recon_id", "filters": filters}

    def aggregate(self, identity, field="content_type", filters=None):
        docs = self.search(identity, filters=filters, limit=10000)["results"]
        buckets = {}
        for doc in docs:
            key = str(doc["fields"].get(field, "UNKNOWN"))
            bucket = buckets.setdefault(key, {"count": 0, "source_bytes": 0})
            bucket["count"] += 1
            bucket["source_bytes"] += int(doc["fields"].get("size_bytes", 0))
        hashes = Counter(doc["fields"].get("content_sha256", doc["fields"].get("content_hash")) for doc in docs)
        return {"field": field, "buckets": buckets, "count": len(docs), "source_bytes": sum(x["source_bytes"] for x in buckets.values()),
            "duplicate_groups": sum(1 for key, count in hashes.items() if key and count > 1), "engine": "LOCAL_BETA", "bounded_at": 10000}

    def recommend_dashboards(self, identity):
        fields = {field for doc in self.documents(identity) for field in doc["fields"]}
        return {"mode": "DETERMINISTIC_SCHEMA_RULES", "recommendations": [
            {"field": field, "metrics": ["count", "sum(size_bytes)"], "reason": "Categorical grouping in the available metadata schema"}
            for field in sorted(fields) if field.endswith("type") or field in {"jurisdiction", "department", "owner", "source_id", "sensitivity"}]}

    def cost(self, identity, rate, months, growth_percent=0):
        if rate < 0 or not 1 <= months <= 120 or not -100 < growth_percent <= 100:
            raise ValueError("invalid cost inputs")
        count = self.aggregate(identity)["source_bytes"]
        gib = count / (1024 ** 3)
        series = [{"month": m, "gib": round(gib * (1 + growth_percent / 100) ** (m - 1), 12),
            "estimated_cost": round(gib * (1 + growth_percent / 100) ** (m - 1) * rate, 4)} for m in range(1, months + 1)]
        return {"mode": "USER_SUPPLIED_RATE_ESTIMATE", "unit": "currency/GiB-month", "source_bytes": count, "series": series,
            "excludes": ["requests", "retrieval", "egress", "replication", "minimum duration", "tax"]}

    def schema(self, identity, name, fields):
        if not fields or any(kind not in {"string", "integer", "boolean", "date", "text"} for kind in fields.values()):
            raise ValueError("schema requires supported field types")
        version = int(self.db.scalar("SELECT MAX(version) FROM beta_schemas WHERE tenant_id=? AND name=?", (identity.tenant, name), 0) or 0) + 1
        self.db.execute("INSERT INTO beta_schemas VALUES(?,?,?,?,?,?)", (uid(), identity.tenant, name, version, self.db.dumps(fields), now()))
        return {"name": name, "version": version, "metadata_docvalues": [key for key, kind in fields.items() if kind != "text"], "fulltext_fields": [key for key, kind in fields.items() if kind == "text"], "reindex_required": False, "note": "Versioned registry; existing projection metadata is unchanged. External Solr schema changes need separate validation."}

    def index_plan(self, body):
        docs, source_bytes = int(body["documents"]), int(body["source_bytes"])
        if docs <= 0 or source_bytes < 0:
            raise ValueError("positive document count and nonnegative source bytes required")
        per_shard = int(body.get("max_documents_per_shard", 10_000_000))
        size_gib = float(body.get("max_index_gib_per_shard", 30))
        expansion = float(body.get("estimated_index_ratio", .25))
        if per_shard <= 0 or size_gib <= 0 or expansion <= 0:
            raise ValueError("capacity estimates must be positive")
        shards = max(1, math.ceil(docs / per_shard), math.ceil(source_bytes * expansion / (size_gib * 1024 ** 3)))
        return {"mode": "PLAN_ONLY", "shards": shards, "partition": "DATE_AND_CAPACITY" if body.get("date_field") else "CAPACITY_GENERATION",
            "date_field": body.get("date_field"), "hot_days": int(body.get("hot_days", 90)),
            "metadata_collection": body.get("name", "archive") + "_meta_g001", "text_collection": body.get("name", "archive") + "_text_g001",
            "rollover": {"max_documents_per_shard": per_shard, "max_index_gib_per_shard": size_gib},
            "assumption": "Index/source size ratio is supplied, not measured; load tests required before production provisioning."}

    def query_plan(self, identity, text):
        # Supported English templates and explicit field=value syntax; no LLM.
        documents = self.documents(identity)
        known = {key for doc in documents for key in doc["fields"]}
        filters, remaining = [], text
        explicit = list(re.finditer(r'([\w_]+)\s*=\s*("[^"]+"|[\w.-]+)', text))
        for match in explicit:
            field, value = match.group(1), match.group(2).strip('"')
            if field not in known:
                raise ValueError("unknown field: " + field)
            filters.append({"field": field, "op": "eq", "value": value})
            remaining = remaining.replace(match.group(0), "")
        if not explicit:
            for field in ("customer_name", "record_type", "jurisdiction", "department"):
                values = {str(d["fields"][field]) for d in documents if field in d["fields"]}
                matched = [value for value in values if re.search(r'\b' + re.escape(value) + r's?\b', text, re.I)]
                if len(matched) > 1:
                    raise ValueError("ambiguous " + field + ": narrow the request")
                if matched:
                    filters.append({"field":field,"op":"eq","value":matched[0]})
            year = re.search(r'\b(?:from|in|during)\s+(20\d{2})\b', text, re.I)
            selected_year = int(year.group(1)) if year else datetime.now(timezone.utc).year-1 if re.search(r'\blast year\b', text, re.I) else None
            if selected_year and "business_date" in known:
                filters.extend([{"field":"business_date","op":"gte","value":f"{selected_year}-01-01T00:00:00+00:00"},
                    {"field":"business_date","op":"lte","value":f"{selected_year}-12-31T23:59:59.999999+00:00"}])
            about = re.search(r'\b(?:about|containing)\s+(.+)$', text, re.I)
            remaining = about.group(1).strip() if about else "" if filters else text
        return {"mode": "DETERMINISTIC_TEMPLATE_ASSISTANT", "query": remaining.strip(), "filters": filters,
            "requires_confirmation": True, "estimated_scope": len(documents),
            "explanation": "Supported grammar: Find [known customer] [record type] in [jurisdiction] from [year/last year] about [content words], or field=value. Review the plan before execution. No arbitrary Solr syntax or model inference."}

    def new_job(self, identity, kind, request):
        if kind not in {"EXPORT", "EVIDENCE", "CLASSIFY", "PII", "BULK_PLAN", "HOP_RUN"}:
            raise ValueError("unsupported job kind")
        jid = uid()
        request = {**request, "identity": {"tenant": identity.tenant, "actor": identity.actor, "roles": list(identity.roles), "groups": list(identity.groups)}}
        self.db.execute("INSERT INTO beta_jobs(id,tenant_id,actor,kind,status,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (jid, identity.tenant, identity.actor, kind, "QUEUED", self.db.dumps(request), now(), now()))
        return self.job(identity, jid)

    def job(self, identity, jid):
        row = self.db.fetchone("SELECT * FROM beta_jobs WHERE tenant_id=? AND id=?", (identity.tenant, jid))
        if not row or (not identity.admin and row["actor"] != identity.actor):
            raise KeyError(jid)
        row["result"] = self.db.loads(row.pop("result_json"), {})
        row.pop("request_json")
        row.pop("token_hash")
        return row

    def run_job(self, identity, jid):
        self.job(identity, jid)
        with self.db.connection() as conn:
            cur = conn.execute(self.db._sql("UPDATE beta_jobs SET status='RUNNING',updated_at=? WHERE id=? AND status IN ('QUEUED','FAILED')"), (now(), jid))
            if cur.rowcount != 1:
                raise ValueError("job is not queued or retryable")
        row = self.db.fetchone("SELECT * FROM beta_jobs WHERE id=?", (jid,))
        request = self.db.loads(row["request_json"], {})
        snapshot = request.pop("identity")
        owner = Identity(snapshot["tenant"], snapshot["actor"], tuple(snapshot["roles"]), tuple(snapshot["groups"]))
        try:
            docs = self.search(owner, request.get("query", ""), request.get("filters"), 10000)["results"]
            if row["kind"] in {"EXPORT", "EVIDENCE"}:
                result = self._export(owner, jid, row["kind"], docs, request)
            elif row["kind"] == "CLASSIFY":
                result = self.classify(owner, docs)
            elif row["kind"] == "PII":
                result = self.scan_pii(owner, docs)
            elif row["kind"] == "HOP_RUN":
                from .hop import run_pipeline
                manifest = self.root / (jid + ".hop-input.json")
                manifest.write_text(self.db.dumps({"job_id": jid, "records": docs}))
                shared = os.getenv("AMP_HOP_SHARED_ROOT", "")
                if not shared:
                    raise ValueError("configure AMP_HOP_SHARED_ROOT to a mount visible on Hop Server")
                result = run_pipeline(request["pipeline"], {"AMP_INPUT_MANIFEST": str(Path(shared) / manifest.name), "AMP_JOB_ID": jid})
            else:
                result = {"mode": "PLAN_ONLY", "candidates": [{"source_id": d["source_id"], "recon_id": d["recon_id"], "version": d["source_version"]} for d in docs],
                    "action": request.get("action", "HOP"), "pipeline": request.get("pipeline"),
                    "reason": "Native mutation requires a qualified executor, current source verification and approval. No object was modified."}
                (self.root / (jid + ".manifest.json")).write_text(self.db.dumps(result))
            self.db.execute("UPDATE beta_jobs SET status='COMPLETE',result_json=?,updated_at=? WHERE id=?", (self.db.dumps(result), now(), jid))
        except Exception as exc:
            (self.root / (jid + ".zip")).unlink(missing_ok=True)
            self.db.execute("UPDATE beta_jobs SET status='FAILED',result_json=?,updated_at=? WHERE id=?", (self.db.dumps({"error": str(exc)}), now(), jid))
        return self.job(identity, jid)

    def _export(self, identity, jid, kind, docs, request):
        ttl = int(request.get("ttl_seconds", 3600))
        if not 60 <= ttl <= 86400:
            raise ValueError("export lifetime must be 60..86400 seconds")
        path = self.root / (jid + ".zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", self.db.dumps(docs))
            flat = io.StringIO()
            keys = sorted({key for doc in docs for key in doc["fields"]} - {"source_id", "recon_id"})
            writer = csv.DictWriter(flat, fieldnames=["source_id", "recon_id"] + keys)
            writer.writeheader()
            for doc in docs:
                values = {key: doc["fields"].get(key, "") for key in keys}
                # Avoid executable formulas in CSV opened in spreadsheet applications.
                values = {key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value for key, value in values.items()}
                writer.writerow({**values, "source_id": doc["source_id"], "recon_id": doc["recon_id"]})
            archive.writestr("metadata.csv", flat.getvalue())
            if kind == "EVIDENCE":
                ids = {d["recon_id"] for d in docs}
                events = self.db.fetchall("SELECT * FROM audit_events WHERE tenant_id=? ORDER BY created_at", (identity.tenant,))
                # Non-admin packs contain only the requester's events on authorized records.
                events = [e for e in events if e.get("recon_id") in ids and (identity.admin or e["actor"] == identity.actor)]
                archive.writestr("access-audit.json", self.db.dumps(events))
                receipts = [self.archive.item(identity.tenant, d["recon_id"])["receipt"] for d in docs if d["fields"].get("archive_id") == d["recon_id"]]
                archive.writestr("archive-receipts.json", self.db.dumps(receipts))
            if request.get("include_documents"):
                for doc in docs:
                    target = self.root / (jid + "-" + doc["recon_id"])
                    try:
                        if doc["fields"].get("archive_id") == doc["recon_id"]:
                            self.archive.download(identity.tenant, doc["recon_id"], target)
                        else:
                            from ..services.storage import backend_from_record
                            group = self.catalog.get_catalogue_group(doc["fields"]["source_group_id"])
                            if group["tenant_id"] != identity.tenant or group["storage_id"] != doc["source_id"]:
                                raise PermissionError("source ownership mismatch")
                            backend = backend_from_record(self.catalog.backend_record_for_group(group["id"]))
                            data,stat = backend.get_with_stat(doc["name"],doc["source_version"])
                            if hashlib.sha256(data).hexdigest() != doc["fields"]["content_sha256"]:
                                raise ValueError("source document changed since indexing")
                            target.write_bytes(data)
                            self.catalog.audit(identity.tenant,"SOURCE_EXPORT_READ",actor=identity.actor,recon_id=doc["recon_id"],details={"version":doc["source_version"]})
                        archive.write(target, "documents/" + doc["recon_id"] + "/" + Path(doc["name"]).name)
                    finally:
                        target.unlink(missing_ok=True)
        # Token is minted on demand, avoiding a persisted plaintext capability.
        self.db.execute("UPDATE beta_jobs SET expires_at=? WHERE id=?", (time.time() + ttl, jid))
        return {"records": len(docs), "bytes": path.stat().st_size, "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "download_ready": True, "mode": "LOCAL_AUTHORIZED_EXPORT", "bounded_at": 10000,
            "authorized_refs": [[doc["source_id"], doc["recon_id"]] for doc in docs]}

    def download_token(self, identity, jid):
        row = self.job(identity, jid)
        if row["status"] != "COMPLETE" or row["expires_at"] <= time.time() or not row["result"].get("download_ready"):
            raise ValueError("export is unavailable or expired")
        allowed = self.authorized_ids(identity)
        if any(tuple(ref) not in allowed for ref in row["result"].get("authorized_refs", [])):
            raise PermissionError("export access changed; regenerate the export")
        token = secrets.token_urlsafe(32)
        self.db.execute("UPDATE beta_jobs SET token_hash=? WHERE id=?", (hashlib.sha256(token.encode()).hexdigest(), jid))
        return {"token": token, "expires_at": row["expires_at"]}

    def download_path(self, identity, jid, token):
        row = self.job(identity, jid)
        allowed = self.authorized_ids(identity)
        if any(tuple(ref) not in allowed for ref in row["result"].get("authorized_refs", [])):
            raise PermissionError("export access changed; regenerate the export")
        stored = self.db.fetchone("SELECT token_hash FROM beta_jobs WHERE id=?", (jid,))
        if row["status"] != "COMPLETE" or row["expires_at"] <= time.time() or not secrets.compare_digest(stored["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise PermissionError("download capability is invalid or expired")
        self.catalog.audit(identity.tenant, "EXPORT_DOWNLOAD", actor=identity.actor, details={"job_id": jid})
        return self.root / (jid + ".zip")

    def classify(self, identity, docs):
        rules = self.db.fetchall("SELECT * FROM beta_classifiers WHERE tenant_id=?", (identity.tenant,))
        count = 0
        for doc in docs:
            fields = doc["fields"]
            for rule in rules:
                if self.matches(fields, self.db.loads(rule["selector_json"], {})):
                    fields.update(self.db.loads(rule["tags_json"], {}))
                    count += 1
            self.project(identity.tenant, doc["source_id"], doc["recon_id"], fields)
        return {"mode": "RULE_BASED_CLASSIFICATION", "documents": len(docs), "rule_matches": count, "native_tags_updated": False}

    def scan_pii(self, identity, docs):
        import regex
        rules = self.db.fetchall("SELECT * FROM pii_rules WHERE tenant_id=? AND enabled=1", (identity.tenant,))
        matches_count = failures = 0
        for doc in docs:
            for rule in rules:
                for field in self.db.loads(rule["fields_json"], []):
                    if field in {"content", "text_content"}:
                        value = "\n".join(row["text_content"] for row in self.db.fetchall("SELECT text_content FROM search_documents WHERE tenant_id=? AND source_id=? AND recon_id=?", (identity.tenant, doc["source_id"], doc["recon_id"])))
                    else:
                        value = str(doc["fields"].get(field, ""))
                    # Remove stale finding for this evaluated rule/field/revision.
                    self.db.execute("DELETE FROM pii_findings WHERE tenant_id=? AND rule_id=? AND source_id=? AND recon_id=? AND field_name=?", (identity.tenant, rule["id"], doc["source_id"], doc["recon_id"], field))
                    try:
                        found = list(regex.finditer(rule["pattern"], value[:1_000_000], regex.IGNORECASE, timeout=.05))
                    except TimeoutError:
                        failures += 1
                        continue
                    if not found:
                        continue
                    matches_count += len(found)
                    self.db.execute("INSERT INTO pii_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uid(), identity.tenant, rule["id"], doc["source_id"], doc["fields"].get("container_name", "archive"), doc["recon_id"], doc["name"], field, len(found), rule["classification"], "[REDACTED]", now()))
                    doc["fields"]["sensitivity"] = rule["classification"]
                    self.project(identity.tenant, doc["source_id"], doc["recon_id"], doc["fields"])
        return {"documents_scanned": len(docs), "matches": matches_count, "failed_evaluations": failures, "coverage": "CONFIGURED_FIELDS_ONLY"}

    def reproject(self, identity, schema_name, changes):
        schema = self.db.fetchone("SELECT * FROM beta_schemas WHERE tenant_id=? AND name=? ORDER BY version DESC LIMIT 1", (identity.tenant, schema_name))
        if not schema:
            raise ValueError("schema not found")
        types = self.db.loads(schema["fields_json"], {})
        changed = 0
        for doc in self.documents(identity):
            fields = dict(doc["fields"])
            for target, source in changes.items():
                if target not in types or types[target] == "text":
                    raise ValueError("metadata reprojection requires a declared non-text target")
                value = fields.get(source)
                if value is None:
                    continue
                if types[target] == "integer":
                    value = int(value)
                elif types[target] == "boolean":
                    if value not in (True, False, "true", "false"):
                        raise ValueError("invalid boolean")
                    value = value in (True, "true")
                elif types[target] == "date":
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("date requires timezone")
                    value = parsed.astimezone(timezone.utc).isoformat()
                else:
                    value = str(value)
                fields[target] = value
            self.project(identity.tenant, doc["source_id"], doc["recon_id"], fields)
            self.db.execute("UPDATE document_projection SET schema_version=? WHERE tenant_id=? AND source_id=? AND recon_id=?", (schema["version"], identity.tenant, doc["source_id"], doc["recon_id"]))
            changed += 1
        return {"documents": changed, "schema_version": schema["version"], "payloads_reread": 0, "text_reindexed": False}

    def optimizer(self, identity, rules):
        plans = []
        for doc in self.documents(identity):
            for rule in rules:
                if self.matches(doc["fields"], rule["selector"]):
                    plans.append({"source_id": doc["source_id"], "recon_id": doc["recon_id"],
                        "destination": rule["destination"], "reason": rule.get("name", "matched rule"),
                        "requires_native_policy_verification": True})
                    break
        return {"mode": "PLAN_ONLY", "actions": plans, "objects_moved": 0}

    def governance_plan(self, identity, trigger_field="business_date"):
        policies = self.db.fetchall("SELECT * FROM governance_policies WHERE tenant_id=? AND enabled=1 ORDER BY priority", (identity.tenant,))
        plans = []
        for doc in self.documents(identity):
            for policy in policies:
                selector = self.db.loads(policy["selector_json"], {})
                if not self.matches(doc["fields"], selector):
                    continue
                decision = {"source_id": doc["source_id"], "recon_id": doc["recon_id"], "policy": policy["name"],
                    "action": policy["action"], "status": "PLANNED", "native_verified": False}
                if policy["action"] in {"RETAIN", "OBJECT_LOCK"}:
                    value = doc["fields"].get(trigger_field)
                    try:
                        from datetime import timedelta
                        base = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                        if base.tzinfo is None:
                            raise ValueError("timezone required")
                        decision["retain_until"] = (base + timedelta(days=policy["retention_days"])).isoformat()
                    except ValueError:
                        decision["status"] = "MISSING_OR_INVALID_TRIGGER"
                plans.append(decision)
        self.catalog.audit(identity.tenant, "GOVERNANCE_PLAN", actor=identity.actor, details={"decisions": len(plans)})
        return {"mode": "PLAN_ONLY", "decisions": plans, "source_mutations": 0, "trigger_field": trigger_field}
