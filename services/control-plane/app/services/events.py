from __future__ import annotations
import json

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import time
from typing import Any, Iterable
from urllib.parse import unquote_plus

from .catalog import CatalogService, now, uid
from .storage import backend_from_record
from ..db import Database

NORMALIZED_TOPIC = os.getenv("AMP_KAFKA_STORAGE_TOPIC", "amp.storage.changes")
DLQ_TOPIC = os.getenv("AMP_KAFKA_DLQ_TOPIC", "amp.storage.changes.dlq")
RAW_MINIO_TOPIC = os.getenv("AMP_KAFKA_MINIO_RAW_TOPIC", "amp.raw.minio")


@dataclass
class NormalizedStorageEvent:
    tenant_id: str
    catalogue_group_id: str
    source_type: str
    event_type: str
    object_key: str
    version_id: str = ""
    native_event_id: str = ""
    event_time: str = ""
    sequencer: str = ""
    etag: str = ""
    size_bytes: int | None = None
    content_type: str = ""
    raw: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "tenant_id": self.tenant_id,
            "catalogue_group_id": self.catalogue_group_id,
            "source_type": self.source_type,
            "event_type": self.event_type,
            "object_key": self.object_key,
            "version_id": self.version_id or "",
            "native_event_id": self.native_event_id or "",
            "event_time": self.event_time or now(),
            "sequencer": self.sequencer or "",
            "etag": self.etag or "",
            "size_bytes": self.size_bytes,
            "content_type": self.content_type or "",
            "raw": self.raw or {},
        }


class EventService:
    """Storage change-event normalization, dedupe and targeted catalogue refresh.

    Normal operation is Kafka-backed, but apply_event() is deliberately transport-neutral
    so tests, webhooks and recovery tooling can use the exact same catalogue semantics.
    """

    def __init__(self, db: Database, catalog: CatalogService):
        self.db = db
        self.catalog = catalog

    # ---------- configuration/state ----------
    def configure_capture(self, group_id: str, mode: str, config: dict[str, Any] | None = None,
                          enabled: bool = True) -> dict[str, Any]:
        group = self.catalog.get_catalogue_group(group_id)
        mode = mode.upper()
        allowed = {"GENERIC_KAFKA", "MINIO_KAFKA", "AWS_SQS", "HCP_MQE"}
        if mode not in allowed:
            raise ValueError(f"mode must be one of {sorted(allowed)}")
        existing = self.db.fetchone("SELECT id FROM change_capture_configs WHERE catalogue_group_id=?", (group_id,))
        ts = now()
        if existing:
            self.db.execute(
                "UPDATE change_capture_configs SET mode=?,enabled=?,config_json=?,status=?,updated_at=? WHERE id=?",
                (mode, bool(enabled), self.db.dumps(config or {}), "CONFIGURED", ts, existing["id"]),
            )
            cid = existing["id"]
        else:
            cid = uid()
            self.db.execute(
                """INSERT INTO change_capture_configs(id,tenant_id,catalogue_group_id,mode,enabled,config_json,checkpoint_json,status,consumer_lag,last_source_event_at,last_applied_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, group["tenant_id"], group_id, mode, bool(enabled), self.db.dumps(config or {}),
                 self.db.dumps({}), "CONFIGURED", 0, None, None, ts, ts),
            )
        self.catalog.audit(group["tenant_id"], "CHANGE_CAPTURE_CONFIG", group_id=group_id,
                           details={"mode": mode, "enabled": enabled, "config": self._safe_config(config or {})})
        return self.get_capture(group_id)

    @staticmethod
    def _safe_config(config: dict[str, Any]) -> dict[str, Any]:
        hidden = {"secret", "secret_key", "password", "token", "access_key"}
        return {k: ("••••••••" if k.lower() in hidden and v else v) for k, v in config.items()}

    def get_capture(self, group_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM change_capture_configs WHERE catalogue_group_id=?", (group_id,))
        if not row:
            return {"catalogue_group_id": group_id, "mode": "NONE", "enabled": False, "status": "NOT_CONFIGURED"}
        row["config"] = self._safe_config(self.db.loads(row.pop("config_json", "{}"), {}))
        row["checkpoint"] = self.db.loads(row.pop("checkpoint_json", "{}"), {})
        return row

    def list_captures(self, tenant_id: str) -> list[dict[str, Any]]:
        groups = self.db.fetchall("SELECT id FROM catalogue_groups WHERE tenant_id=? ORDER BY name", (tenant_id,))
        return [self.get_capture(g["id"]) for g in groups]

    def update_checkpoint(self, group_id: str, checkpoint: dict[str, Any], *,
                          source_event_at: str | None = None, applied_at: str | None = None,
                          lag: int | None = None, status: str = "HEALTHY") -> None:
        existing = self.db.fetchone("SELECT id FROM change_capture_configs WHERE catalogue_group_id=?", (group_id,))
        if not existing:
            return
        self.db.execute(
            """UPDATE change_capture_configs SET checkpoint_json=?,status=?,consumer_lag=COALESCE(?,consumer_lag),
               last_source_event_at=COALESCE(?,last_source_event_at),last_applied_at=COALESCE(?,last_applied_at),updated_at=? WHERE catalogue_group_id=?""",
            (self.db.dumps(checkpoint), status, lag, source_event_at, applied_at, now(), group_id),
        )

    # ---------- normalized event handling ----------
    def idempotency_key(self, event: dict[str, Any]) -> str:
        native = str(event.get("native_event_id") or "")
        if native:
            raw = f"{event.get('source_type','')}|{event.get('catalogue_group_id','')}|{native}"
        else:
            raw = "|".join(str(event.get(k) or "") for k in (
                "source_type", "catalogue_group_id", "event_type", "object_key", "version_id", "event_time", "sequencer"
            ))
        return hashlib.sha256(raw.encode()).hexdigest()

    def record_received(self, event: dict[str, Any], *, topic: str | None = None,
                        partition: int | None = None, offset: int | None = None) -> tuple[str, bool, str]:
        idem = self.idempotency_key(event)
        existing = self.db.fetchone("SELECT id,status FROM storage_events WHERE idempotency_key=?", (idem,))
        if existing:
            return existing["id"], False, existing.get("status") or "UNKNOWN"
        eid = uid()
        self.db.execute(
            """INSERT INTO storage_events(id,tenant_id,catalogue_group_id,source_type,native_event_id,idempotency_key,event_type,
               object_key,version_id,event_time,sequencer,payload_json,status,kafka_topic,kafka_partition,kafka_offset,error_message,received_at,applied_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, event["tenant_id"], event["catalogue_group_id"], event["source_type"], event.get("native_event_id") or "",
             idem, event["event_type"], event["object_key"], event.get("version_id") or "", event.get("event_time") or now(),
             event.get("sequencer") or "", self.db.dumps(event), "RECEIVED", topic, partition, offset, None, now(), None),
        )
        return eid, True, "RECEIVED"

    @staticmethod
    def _lag_seconds(event_time: str | None) -> int:
        if not event_time:
            return 0
        try:
            dt = datetime.fromisoformat(str(event_time).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
        except Exception:
            return 0

    @staticmethod
    def _sequencer_value(value: str | None) -> int | None:
        if not value:
            return None
        try:
            return int(str(value), 16)
        except Exception:
            return None

    def _stale_by_sequencer(self, event: dict[str, Any]) -> bool:
        seq = self._sequencer_value(event.get("sequencer"))
        if seq is None:
            return False
        row = self.db.fetchone(
            "SELECT last_sequencer FROM event_object_watermarks WHERE catalogue_group_id=? AND object_key=? AND source_type=?",
            (event["catalogue_group_id"], event["object_key"], event["source_type"]),
        )
        if not row:
            return False
        last = self._sequencer_value(row.get("last_sequencer"))
        return last is not None and seq <= last

    def _record_object_watermark(self, event: dict[str, Any]) -> None:
        key = (event["catalogue_group_id"], event["object_key"], event["source_type"])
        existing = self.db.fetchone(
            "SELECT catalogue_group_id FROM event_object_watermarks WHERE catalogue_group_id=? AND object_key=? AND source_type=?", key
        )
        vals = (event.get("sequencer") or "", event.get("event_time") or now(), event.get("native_event_id") or "", now())
        if existing:
            self.db.execute(
                "UPDATE event_object_watermarks SET last_sequencer=?,last_event_time=?,last_native_event_id=?,updated_at=? WHERE catalogue_group_id=? AND object_key=? AND source_type=?",
                (*vals, *key),
            )
        else:
            self.db.execute(
                "INSERT INTO event_object_watermarks(catalogue_group_id,object_key,source_type,last_sequencer,last_event_time,last_native_event_id,updated_at) VALUES(?,?,?,?,?,?,?)",
                (*key, *vals),
            )

    def apply_event(self, event: dict[str, Any], *, topic: str | None = None,
                    partition: int | None = None, offset: int | None = None, force: bool = False) -> dict[str, Any]:
        required = ("tenant_id", "catalogue_group_id", "source_type", "event_type", "object_key")
        missing = [k for k in required if not event.get(k)]
        if missing:
            raise ValueError(f"normalized storage event missing: {', '.join(missing)}")
        group = self.catalog.get_catalogue_group(event["catalogue_group_id"])
        if group["tenant_id"] != event["tenant_id"]:
            raise ValueError("event tenant/catalogue mismatch")
        event_id, is_new, existing_status = self.record_received(event, topic=topic, partition=partition, offset=offset)
        replayable = existing_status in {"FAILED", "RECEIVED"}
        if not is_new and not (force and replayable):
            return {"storage_event_id": event_id, "status": "DUPLICATE", "applied": False}
        if force and replayable:
            self.db.execute("UPDATE storage_events SET status='RECEIVED',error_message=NULL WHERE id=?", (event_id,))

        # Everything after durable receipt creation is inside the failure boundary so
        # classifier/watermark failures cannot strand a RECEIVED row while Kafka advances.
        try:
            if self._stale_by_sequencer(event):
                self.db.execute("UPDATE storage_events SET status='IGNORED_STALE',applied_at=? WHERE id=?", (now(), event_id))
                return {"storage_event_id": event_id, "status": "IGNORED_STALE", "applied": False}

            typ = event["event_type"].upper()
            if typ in {"OBJECT_CREATED", "OBJECT_UPDATED", "OBJECT_METADATA_CHANGED", "OBJECT_RESTORED"}:
                backend = backend_from_record(self.catalog.backend_record_for_group(group["id"]))
                stat = backend.head(event["object_key"])
                if not stat:
                    raise FileNotFoundError(f"object not found during targeted refresh: {event['object_key']}")
                version_id = stat.version_id or event.get("version_id") or ""
                obj = self.catalog.upsert_catalogue_object(
                    group_id=group["id"], object_key=event["object_key"], version_id=version_id,
                    size_bytes=stat.size, etag=stat.etag, checksum=stat.checksum_sha256,
                    content_type=stat.content_type, source_mode="EVENT",
                    metadata={"last_event_type": typ, "last_native_event_id": event.get("native_event_id") or "",
                              "backend_response": stat.backend.raw, "backend_status": stat.backend.status,
                              "backend_request_id": stat.backend.request_id, "backend_compliance": stat.compliance},
                )
                action = "CATALOGUE_EVENT_UPSERT"
            elif typ in {"OBJECT_DELETED", "OBJECT_PURGED", "OBJECT_PRUNED"}:
                obj = self.catalog.tombstone_by_key(group["id"], event["object_key"], event.get("version_id") or "",
                                                    source_mode="EVENT")
                action = "CATALOGUE_EVENT_TOMBSTONE"
            else:
                raise ValueError(f"unsupported normalized event_type: {typ}")

            applied = now()
            self.db.execute("UPDATE storage_events SET status='APPLIED',applied_at=?,error_message=NULL WHERE id=?", (applied, event_id))
            self._record_object_watermark(event)
            lag_seconds = self._lag_seconds(event.get("event_time"))
            self.update_checkpoint(group["id"], {
                "last_event_id": event_id,
                "last_native_event_id": event.get("native_event_id") or "",
                "last_kafka_topic": topic,
                "last_kafka_partition": partition,
                "last_kafka_offset": offset,
                "event_lag_seconds": lag_seconds,
            }, source_event_at=event.get("event_time") or now(), applied_at=applied, lag=lag_seconds, status="HEALTHY")
            self.catalog.audit(group["tenant_id"], action, group_id=group["id"],
                               object_id=obj.get("id") if obj else None,
                               recon_id=obj.get("recon_id") if obj else None,
                               details={"object_key": event["object_key"], "event_type": typ,
                                        "native_event_id": event.get("native_event_id") or ""})
            return {"storage_event_id": event_id, "status": "APPLIED", "applied": True,
                    "object": obj, "recon_id": obj.get("recon_id") if obj else None}
        except Exception as exc:
            self.db.execute("UPDATE storage_events SET status='FAILED',error_message=? WHERE id=?", (str(exc), event_id))
            self._dead_letter(event_id, event, str(exc))
            self.update_checkpoint(group["id"], {}, source_event_at=event.get("event_time") or now(), status="DEGRADED")
            raise

    def _dead_letter(self, event_id: str, event: dict[str, Any], error: str) -> None:
        self.db.execute(
            "INSERT INTO event_dead_letters(id,storage_event_id,catalogue_group_id,payload_json,error_message,status,created_at,replayed_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid(), event_id, event.get("catalogue_group_id"), self.db.dumps(event), error, "OPEN", now(), None),
        )

    def list_events(self, tenant_id: str, group_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM storage_events WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if group_id:
            sql += " AND catalogue_group_id=?"; params.append(group_id)
        sql += " ORDER BY received_at DESC LIMIT ?"; params.append(limit)
        rows = self.db.fetchall(sql, params)
        for r in rows:
            r["payload"] = self.db.loads(r.pop("payload_json", "{}"), {})
        return rows

    def list_dlq(self, group_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM event_dead_letters WHERE 1=1"
        params: list[Any] = []
        if group_id:
            sql += " AND catalogue_group_id=?"; params.append(group_id)
        sql += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
        rows = self.db.fetchall(sql, params)
        for r in rows:
            r["payload"] = self.db.loads(r.pop("payload_json", "{}"), {})
        return rows

    # ---------- transport ----------
    @staticmethod
    def _producer(bootstrap: str):
        from confluent_kafka import Producer  # type: ignore
        return Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True, "acks": "all"})

    def publish(self, event: dict[str, Any], bootstrap: str | None = None, topic: str = NORMALIZED_TOPIC) -> None:
        bootstrap = bootstrap or os.getenv("AMP_KAFKA_BOOTSTRAP", "kafka:9092")
        producer = self._producer(bootstrap)
        key = f"{event.get('catalogue_group_id','')}|{event.get('object_key','')}"
        error: list[str] = []
        producer.produce(topic, key=key.encode(), value=json.dumps(event, default=str).encode(),
                         on_delivery=lambda err, msg: error.append(str(err)) if err else None)
        producer.flush(10)
        if error:
            raise RuntimeError(error[0])

    def publish_dlq(self, event: dict[str, Any], error: str, bootstrap: str | None = None) -> None:
        payload = {"failed_event": event, "error": error, "failed_at": now()}
        try:
            self.publish(payload, bootstrap=bootstrap, topic=DLQ_TOPIC)
        except Exception:
            pass

    def replay_event(self, storage_event_id: str, *, publish: bool = True, bootstrap: str | None = None) -> dict[str, Any]:
        row = self.db.fetchone("SELECT payload_json,status FROM storage_events WHERE id=?", (storage_event_id,))
        if not row:
            raise KeyError(storage_event_id)
        event = self.db.loads(row.get("payload_json"), {})
        if publish:
            self.publish(event, bootstrap=bootstrap, topic=NORMALIZED_TOPIC)
            return {"status": "REQUEUED", "storage_event_id": storage_event_id}
        return self.apply_event(event, force=True)

    def replay_dlq(self, dlq_id: str, *, publish: bool = True, bootstrap: str | None = None) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM event_dead_letters WHERE id=?", (dlq_id,))
        if not row:
            raise KeyError(dlq_id)
        event = self.db.loads(row.get("payload_json"), {})
        if publish:
            self.publish(event, bootstrap=bootstrap, topic=NORMALIZED_TOPIC)
            result = {"status": "REQUEUED", "dlq_id": dlq_id}
        else:
            result = self.apply_event(event, force=True)
        self.db.execute("UPDATE event_dead_letters SET status='REPLAYED',replayed_at=? WHERE id=?", (now(), dlq_id))
        return result

    # ---------- adapter normalization ----------
    def normalize_s3_notification(self, payload: dict[str, Any], *, group_id: str,
                                  tenant_id: str, source_type: str) -> list[dict[str, Any]]:
        """Normalize AWS S3 / MinIO S3-notification shaped payloads."""
        records = payload.get("Records") or []
        out: list[dict[str, Any]] = []
        for rec in records:
            evname = str(rec.get("eventName") or "")
            if "ObjectCreated" in evname:
                et = "OBJECT_CREATED"
            elif "ObjectRemoved" in evname:
                et = "OBJECT_DELETED"
            elif "ObjectTagging" in evname or "ObjectAcl" in evname:
                et = "OBJECT_METADATA_CHANGED"
            else:
                continue
            s3 = rec.get("s3") or {}
            obj = s3.get("object") or {}
            response = rec.get("responseElements") or {}
            event = NormalizedStorageEvent(
                tenant_id=tenant_id, catalogue_group_id=group_id, source_type=source_type,
                event_type=et, object_key=unquote_plus(str(obj.get("key") or "")),
                version_id=str(obj.get("versionId") or ""),
                native_event_id=str(rec.get("eventID") or response.get("x-amz-request-id") or ""),
                event_time=str(rec.get("eventTime") or now()), sequencer=str(obj.get("sequencer") or ""),
                etag=str(obj.get("eTag") or ""), size_bytes=int(obj["size"]) if obj.get("size") is not None else None,
                raw=rec,
            ).as_dict()
            if event["object_key"]:
                out.append(event)
        return out

    def normalize_eventbridge_s3(self, payload: dict[str, Any], *, group_id: str,
                                 tenant_id: str) -> list[dict[str, Any]]:
        detail = payload.get("detail") or {}
        obj = detail.get("object") or {}
        detail_type = str(payload.get("detail-type") or "")
        reason = str(detail.get("reason") or detail_type)
        lower = reason.lower()
        if "delete" in lower:
            typ = "OBJECT_DELETED"
        elif "tag" in lower or "acl" in lower:
            typ = "OBJECT_METADATA_CHANGED"
        else:
            typ = "OBJECT_CREATED"
        key = unquote_plus(str(obj.get("key") or ""))
        if not key:
            return []
        return [NormalizedStorageEvent(
            tenant_id=tenant_id, catalogue_group_id=group_id, source_type="AWS_EVENTBRIDGE",
            event_type=typ, object_key=key, version_id=str(obj.get("version-id") or ""),
            native_event_id=str(payload.get("id") or ""), event_time=str(payload.get("time") or now()),
            sequencer=str(obj.get("sequencer") or ""), etag=str(obj.get("etag") or ""),
            size_bytes=int(obj["size"]) if obj.get("size") is not None else None, raw=payload,
        ).as_dict()]

    def normalize_hcp_operations(self, operations: Iterable[dict[str, Any]], *, group_id: str,
                                 tenant_id: str) -> list[dict[str, Any]]:
        """Normalize an HCP MQE operation result after connector-specific retrieval.

        Expected beta adapter fields are deliberately simple: operation/type, urlName/object_key,
        version/versionId, changeTime/event_time and id/operationId. Sites can map native MQE XML/JSON
        into this canonical shape without coupling the catalogue worker to HCP query syntax.
        """
        out: list[dict[str, Any]] = []
        for op in operations:
            native_type = str(op.get("operation") or op.get("type") or "").upper()
            if native_type in {"DELETE", "DELETED", "DISPOSE", "DISPOSITION", "PURGE", "PRUNE"}:
                typ = "OBJECT_DELETED" if native_type in {"DELETE", "DELETED", "DISPOSE", "DISPOSITION"} else f"OBJECT_{native_type}D"
            elif native_type in {"CREATE", "CREATED"}:
                typ = "OBJECT_CREATED"
            else:
                typ = "OBJECT_UPDATED"
            key = str(op.get("object_key") or op.get("urlName") or op.get("key") or "")
            if not key:
                continue
            out.append(NormalizedStorageEvent(
                tenant_id=tenant_id, catalogue_group_id=group_id, source_type="HCP_MQE",
                event_type=typ, object_key=key,
                version_id=str(op.get("version_id") or op.get("versionId") or op.get("version") or ""),
                native_event_id=str(op.get("operation_id") or op.get("operationId") or op.get("id") or ""),
                event_time=str(op.get("change_time") or op.get("changeTime") or op.get("event_time") or now()),
                raw=op,
            ).as_dict())
        return out
