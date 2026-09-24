from __future__ import annotations
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from ..db import Database
from .catalog import CatalogService, now, uid
from .storage import backend_from_record


class ProcessingService:
    """Lab search/AI pipeline and object retrieval.

    The indexing path intentionally scans storage directly. It does not read catalogue_objects.
    In production this responsibility moves to Apache Hop + Solr/vector services while retaining
    the exact same deterministic recon_id algorithm.
    """

    PIPELINE_VERSION = "amp-hop-contract-v2"
    EMBEDDING_MODEL = "amp-hash-embed-v2"

    def __init__(self, db: Database, catalog: CatalogService):
        self.db = db
        self.catalog = catalog

    def read_catalogue_object(self, oid: str) -> tuple[bytes, dict]:
        obj = self.catalog.get_catalogue_object(oid)
        # A tombstone is an administrative reconciliation record only. Once the source
        # delete has been confirmed, never attempt a source GET for that record.
        if str(obj.get("lifecycle_state") or "").upper() == "TOMBSTONED":
            raise FileNotFoundError(f"source payload deleted: {obj.get('object_key')}")
        src_backend = backend_from_record(self.catalog.backend_record_for_group(obj["catalogue_group_id"]))
        data = src_backend.get(self.catalog.payload_key_for(obj))
        self.catalog.audit(obj["tenant_id"] if "tenant_id" in obj else self.catalog.get_catalogue_group(obj["catalogue_group_id"])["tenant_id"],
                           "OBJECT_READ", group_id=obj["catalogue_group_id"], object_id=oid,
                           recon_id=obj["recon_id"], details={"key": obj["object_key"], "payload_key": self.catalog.payload_key_for(obj)})
        return data, obj

    def extract_text(self, data: bytes, content_type: str, name: str) -> tuple[str, dict[str, Any]]:
        if settings.tika_url:
            try:
                r = httpx.put(settings.tika_url.rstrip("/") + "/tika", content=data,
                              headers={"Accept": "text/plain"}, timeout=60)
                r.raise_for_status()
                return r.text, {"extractor": "tika", "status": r.status_code}
            except Exception:
                pass
        ct = (content_type or "").lower()
        suffix = Path(name).suffix.lower()
        if "json" in ct or suffix == ".json":
            try:
                return json.dumps(json.loads(data.decode("utf-8", errors="ignore")), indent=2), {"extractor": "builtin-json"}
            except Exception:
                pass
        if "xml" in ct or suffix in {".xml", ".html", ".htm"}:
            text = re.sub(r"<[^>]+>", " ", data.decode("utf-8", errors="ignore"))
            return re.sub(r"\s+", " ", text).strip(), {"extractor": "builtin-markup"}
        if "pdf" in ct or suffix == ".pdf":
            try:
                import pypdf
                rd = pypdf.PdfReader(io.BytesIO(data))
                return "\n".join((p.extract_text() or "") for p in rd.pages), {"extractor": "pypdf", "pages": len(rd.pages)}
            except Exception:
                pass
        if suffix == ".docx":
            try:
                from docx import Document
                d = Document(io.BytesIO(data))
                return "\n".join(p.text for p in d.paragraphs), {"extractor": "python-docx"}
            except Exception:
                pass
        return data.decode("utf-8", errors="ignore"), {"extractor": "builtin-text"}

    def embedding(self, text: str, dims: int = 64) -> list[float]:
        vec = [0.0] * dims
        for token in re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()):
            h = hashlib.sha256(token.encode()).digest()
            idx = int.from_bytes(h[:4], "big") % dims
            vec[idx] += 1 if h[4] % 2 else -1
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]

    def index_source(self, group_id: str, prefix: str = "", limit: int = 100_000) -> dict:
        """Simulate connector -> extraction -> search projection without catalogue reads."""
        group = self.catalog.get_catalogue_group(group_id)
        backend = backend_from_record(self.catalog.backend_record_for_group(group_id))
        processed = indexed = 0
        errors: list[dict[str, str]] = []
        native_items = list(backend.list(prefix))
        work = [(item.key, item.key, item.version_id or "", item.content_type) for item in native_items]

        for logical_key, payload_key, version_id, content_type in work:
            if processed >= limit:
                break
            processed += 1
            try:
                stat = backend.head(payload_key)
                if not stat:
                    raise FileNotFoundError(payload_key)
                recon_id = self.catalog.recon_id_for(group, logical_key, version_id or stat.version_id or "")
                data = backend.get(payload_key)
                content_hash = hashlib.sha256(data).hexdigest()
                text, meta = self.extract_text(data[:settings.max_extract_bytes], content_type or stat.content_type, logical_key)
                self.db.execute(
                    "DELETE FROM search_documents WHERE source_id=? AND container_type=? AND container_name=? AND recon_id=?",
                    (group["storage_id"], group["container_type"], group["container_name"], recon_id),
                )
                chunks = 0
                for seq, start in enumerate(range(0, len(text), settings.chunk_chars)):
                    chunk = text[start:start + settings.chunk_chars].strip()
                    if not chunk:
                        continue
                    chunks += 1
                    self.db.execute(
                        """INSERT INTO search_documents(id,tenant_id,source_id,container_type,container_name,recon_id,source_version,object_key,content_hash,chunk_seq,text_content,text_hash,embedding_json,pipeline_version,indexed_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (uid(), group["tenant_id"], group["storage_id"], group["container_type"], group["container_name"],
                         recon_id, version_id or stat.version_id or "", logical_key, content_hash, seq, chunk,
                         hashlib.sha256(chunk.encode()).hexdigest(), self.db.dumps(self.embedding(chunk)),
                         self.PIPELINE_VERSION, now()),
                    )
                self._upsert_ai_artifact(group, recon_id, content_hash, "EMBEDDING_SET", chunks)
                indexed += 1
            except Exception as exc:
                errors.append({"key": logical_key, "error": str(exc)})
        return {"processed": processed, "indexed": indexed, "errors": errors,
                "pipeline": self.PIPELINE_VERSION, "catalogue_dependency": False}

    def _upsert_ai_artifact(self, group: dict, recon_id: str, content_hash: str, artifact_type: str, chunks: int):
        existing = self.db.fetchone(
            "SELECT id FROM ai_artifacts WHERE source_id=? AND container_type=? AND container_name=? AND recon_id=? AND artifact_type=? AND model_name=? AND model_version=?",
            (group["storage_id"], group["container_type"], group["container_name"], recon_id,
             artifact_type, self.EMBEDDING_MODEL, "1"),
        )
        ts = now()
        if existing:
            self.db.execute(
                "UPDATE ai_artifacts SET source_content_hash=?,pipeline_version=?,status='READY',updated_at=? WHERE id=?",
                (content_hash, self.PIPELINE_VERSION, ts, existing["id"]),
            )
        else:
            self.db.execute(
                """INSERT INTO ai_artifacts(id,tenant_id,source_id,container_type,container_name,recon_id,source_content_hash,artifact_type,model_name,model_version,pipeline_version,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid(), group["tenant_id"], group["storage_id"], group["container_type"], group["container_name"],
                 recon_id, content_hash, artifact_type, self.EMBEDDING_MODEL, "1", self.PIPELINE_VERSION,
                 "READY", ts, ts),
            )

    def search(self, tenant: str, query: str, limit: int = 20, dataset_id: str | None = None) -> list[dict]:
        tokens = [t.lower() for t in re.findall(r"[\w-]+", query) if len(t) > 1]
        params: list[Any] = [tenant]
        sql = "SELECT * FROM search_documents WHERE tenant_id=?"
        if dataset_id:
            sql += " AND EXISTS (SELECT 1 FROM dataset_members d WHERE d.dataset_id=? AND d.source_id=search_documents.source_id AND d.container_type=search_documents.container_type AND d.container_name=search_documents.container_name AND d.recon_id=search_documents.recon_id)"
            params.append(dataset_id)
        rows = self.db.fetchall(sql, params)
        qemb = self.embedding(query)
        scored = []
        for r in rows:
            text = r.get("text_content") or ""
            low = (r.get("object_key") or "").lower() + " " + text.lower()
            lexical = sum(low.count(t) for t in tokens)
            emb = self.db.loads(r.get("embedding_json"), []) or []
            semantic = sum(a * b for a, b in zip(qemb, emb)) if emb else 0.0
            score = lexical * 2.0 + max(0, semantic)
            if score > 0 or not tokens:
                scored.append({
                    "recon_id": r["recon_id"], "name": r["object_key"].rsplit("/", 1)[-1],
                    "object_key": r["object_key"], "source_id": r["source_id"], "container_name": r["container_name"],
                    "chunk": r["chunk_seq"], "snippet": text[:420], "lexical_score": lexical,
                    "semantic_score": round(semantic, 4), "score": round(score, 4), "pipeline_version": r["pipeline_version"],
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]
