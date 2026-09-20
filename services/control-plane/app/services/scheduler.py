from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import Database
from .catalog import CatalogService, now, uid
from .operations import OperationsService


def _dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    out = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


class ScheduleService:
    """Schedules administrative catalogue work.

    Change capture itself is continuous and is deliberately NOT represented as a cron-style
    schedule. These schedules cover baseline/integrity scans and reconciliation jobs.
    """

    ALLOWED = {"BASELINE", "INTEGRITY_AUDIT", "STORAGE_RECON", "INDEX_RECON", "AI_RECON", "ALL_RECON"}

    def __init__(self, db: Database, catalog: CatalogService, ops: OperationsService):
        self.db = db
        self.catalog = catalog
        self.ops = ops

    def configure(self, group_id: str, schedule_type: str, interval_minutes: int,
                  enabled: bool = True, next_run_at: str | None = None) -> dict[str, Any]:
        group = self.catalog.get_catalogue_group(group_id)
        typ = schedule_type.upper()
        if typ not in self.ALLOWED:
            raise ValueError(f"schedule_type must be one of {sorted(self.ALLOWED)}")
        if interval_minutes < 1:
            raise ValueError("interval_minutes must be >= 1")
        ts = now()
        next_dt = _dt(next_run_at) if next_run_at else datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)
        next_value = next_dt.isoformat()
        existing = self.db.fetchone(
            "SELECT id FROM catalogue_schedules WHERE catalogue_group_id=? AND schedule_type=?",
            (group_id, typ),
        )
        if existing:
            sid = existing["id"]
            self.db.execute(
                "UPDATE catalogue_schedules SET enabled=?,interval_minutes=?,next_run_at=?,updated_at=? WHERE id=?",
                (bool(enabled), interval_minutes, next_value, ts, sid),
            )
        else:
            sid = uid()
            self.db.execute(
                "INSERT INTO catalogue_schedules(id,tenant_id,catalogue_group_id,schedule_type,enabled,interval_minutes,next_run_at,last_run_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, group["tenant_id"], group_id, typ, bool(enabled), interval_minutes, next_value, None, ts, ts),
            )
        self.catalog.audit(group["tenant_id"], "CATALOGUE_SCHEDULE_CONFIG", group_id=group_id,
                           details={"schedule_type": typ, "interval_minutes": interval_minutes, "enabled": enabled})
        return self.get(sid)

    def get(self, schedule_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM catalogue_schedules WHERE id=?", (schedule_id,))
        if not row:
            raise KeyError(schedule_id)
        return row

    def list(self, tenant_id: str, group_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM catalogue_schedules WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if group_id:
            sql += " AND catalogue_group_id=?"; params.append(group_id)
        sql += " ORDER BY catalogue_group_id,schedule_type"
        return self.db.fetchall(sql, params)

    def due(self, limit: int = 50) -> list[dict[str, Any]]:
        # ISO-8601 UTC strings sort chronologically in SQLite; TIMESTAMPTZ compares natively in PostgreSQL.
        return self.db.fetchall(
            "SELECT * FROM catalogue_schedules WHERE enabled=? AND next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at LIMIT ?",
            (True, now(), limit),
        )

    def run(self, schedule: dict[str, Any]) -> dict[str, Any]:
        group = self.catalog.get_catalogue_group(schedule["catalogue_group_id"])
        typ = schedule["schedule_type"].upper()
        if typ in {"BASELINE", "INTEGRITY_AUDIT"}:
            result = self.ops.discover(group["tenant_id"], group["id"], "")
        else:
            target = {
                "STORAGE_RECON": "STORAGE",
                "INDEX_RECON": "INDEX",
                "AI_RECON": "AI",
                "ALL_RECON": "ALL",
            }[typ]
            result = self.ops.reconcile(group["tenant_id"], group["id"], target)
        interval = int(schedule["interval_minutes"])
        next_value = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat()
        self.db.execute(
            "UPDATE catalogue_schedules SET last_run_at=?,next_run_at=?,updated_at=? WHERE id=?",
            (now(), next_value, now(), schedule["id"]),
        )
        return {"schedule_id": schedule["id"], "schedule_type": typ, "result": result, "next_run_at": next_value}
