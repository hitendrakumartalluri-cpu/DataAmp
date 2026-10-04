from __future__ import annotations
from ..services.catalog import now, uid


class IndexLedger:
    def __init__(self, db):
        self.db = db

    def init_schema(self):
        self.db.execute("""CREATE TABLE IF NOT EXISTS beta_index_state(tenant_id TEXT NOT NULL,source_id TEXT NOT NULL,
            recon_id TEXT NOT NULL,source_version TEXT NOT NULL,source_hash TEXT NOT NULL,status TEXT NOT NULL,
            error TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,
            PRIMARY KEY(tenant_id,source_id,recon_id))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS beta_index_attempt(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,
            source_id TEXT NOT NULL,recon_id TEXT NOT NULL,source_version TEXT NOT NULL,status TEXT NOT NULL,
            error TEXT,created_at TEXT NOT NULL)""")

    def record(self, tenant, source, recon, version, checksum, status, error=None):
        self.db.execute("""INSERT INTO beta_index_state VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id,source_id,recon_id) DO UPDATE SET source_version=excluded.source_version,
            source_hash=excluded.source_hash,status=excluded.status,error=excluded.error,
            attempt_count=beta_index_state.attempt_count+1,updated_at=excluded.updated_at""",
            (tenant, source, recon, version or "", checksum or "", status, error, 1, now()))
        self.db.execute("INSERT INTO beta_index_attempt VALUES(?,?,?,?,?,?,?,?)", (uid(), tenant, source, recon, version or "", status, error, now()))

    def verify_local(self, tenant):
        findings = []
        for row in self.db.fetchall("SELECT * FROM beta_index_state WHERE tenant_id=?", (tenant,)):
            hits = self.db.fetchall("SELECT source_version,content_hash FROM search_documents WHERE tenant_id=? AND source_id=? AND recon_id=?", (tenant, row["source_id"], row["recon_id"]))
            if not hits:
                findings.append({"recon_id": row["recon_id"], "type": "MISSING_FROM_INDEX"})
            elif any(hit["source_version"] != row["source_version"] or hit["content_hash"] != row["source_hash"] for hit in hits):
                findings.append({"recon_id": row["recon_id"], "type": "STALE_INDEX_REVISION"})
        return {"engine": "LOCAL_BETA", "findings": findings}
