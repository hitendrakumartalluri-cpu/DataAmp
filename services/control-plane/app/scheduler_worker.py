from __future__ import annotations
import os
import sys
import time

from .config import settings
from .db import Database
from .services.catalog import CatalogService
from .services.operations import OperationsService
from .services.scheduler import ScheduleService


def main() -> int:
    db = Database(settings.database_url); db.init_schema()
    cat = CatalogService(db); ops = OperationsService(db, cat); schedules = ScheduleService(db, cat, ops)
    sleep_seconds = max(2, int(os.getenv("AMP_SCHEDULER_POLL_SECONDS", "10")))
    print(f"[scheduler] polling every {sleep_seconds}s", flush=True)
    while True:
        for item in schedules.due():
            try:
                result = schedules.run(item)
                print(f"[scheduler] complete {item['schedule_type']} group={item['catalogue_group_id']} next={result['next_run_at']}", flush=True)
            except Exception as exc:
                # Advance the schedule rather than hot-looping a broken source. Operators can inspect the failed job.
                from datetime import datetime, timedelta, timezone
                nxt = (datetime.now(timezone.utc) + timedelta(minutes=int(item['interval_minutes']))).isoformat()
                db.execute("UPDATE catalogue_schedules SET last_run_at=?,next_run_at=?,updated_at=? WHERE id=?",
                           (datetime.now(timezone.utc).isoformat(), nxt, datetime.now(timezone.utc).isoformat(), item['id']))
                print(f"[scheduler] FAILED {item['schedule_type']} group={item['catalogue_group_id']} error={exc}", file=sys.stderr, flush=True)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
