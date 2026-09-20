from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import httpx
from botocore.config import Config

from .config import settings
from .db import Database
from .services.catalog import CatalogService, now
from .services.events import EventService, NORMALIZED_TOPIC, RAW_MINIO_TOPIC


def stack():
    db = Database(settings.database_url)
    db.init_schema()
    cat = CatalogService(db)
    return db, cat, EventService(db, cat)


def consumer_config(group: str) -> dict[str, Any]:
    return {
        "bootstrap.servers": settings.kafka_bootstrap or "kafka:9092",
        "group.id": group,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }


def consume_normalized() -> None:
    from confluent_kafka import Consumer  # type: ignore
    db, cat, events = stack()
    consumer = Consumer(consumer_config(settings.kafka_consumer_group))
    consumer.subscribe([settings.kafka_storage_topic])
    print(f"[event-worker] consuming {settings.kafka_storage_topic} as {settings.kafka_consumer_group}", flush=True)
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"[event-worker] kafka error: {msg.error()}", file=sys.stderr, flush=True)
                continue
            try:
                event = json.loads(msg.value().decode())
                result = events.apply_event(event, topic=msg.topic(), partition=msg.partition(), offset=msg.offset())
                consumer.commit(message=msg, asynchronous=False)
                print(f"[event-worker] {result['status']} {event.get('catalogue_group_id')} {event.get('event_type')} {event.get('object_key')}", flush=True)
            except Exception as exc:
                try:
                    event = json.loads(msg.value().decode())
                    events.publish_dlq(event, str(exc), settings.kafka_bootstrap)
                except Exception:
                    pass
                # Commit poison messages after durable DB/Kafka DLQ recording to prevent a hot loop.
                consumer.commit(message=msg, asynchronous=False)
                print(f"[event-worker] FAILED offset={msg.offset()} error={exc}", file=sys.stderr, flush=True)
    finally:
        consumer.close()


def _infer_group(db: Database, bucket: str, raw_topic: str) -> dict[str, Any] | None:
    # Route through the catalogue group's configured MinIO raw topic, not bucket name alone.
    # Different storage systems commonly reuse the same bucket name, so bucket-only routing is unsafe.
    candidates = _enabled_configs(db, "MINIO_KAFKA")
    matches = [c for c in candidates if c.get("container_name") == bucket and
               str((c.get("config") or {}).get("raw_topic") or "") == raw_topic]
    if len(matches) == 1:
        c = matches[0]
        return {"id": c["catalogue_group_id"], "tenant_id": c["tenant_id"],
                "storage_id": c["storage_id"], "container_name": c["container_name"]}
    return None


def consume_minio_raw() -> None:
    from confluent_kafka import Consumer  # type: ignore
    db, cat, events = stack()
    group_name = os.getenv("AMP_KAFKA_MINIO_ADAPTER_GROUP", "amp-minio-adapter")
    consumer = Consumer(consumer_config(group_name))
    raw_topic = settings.kafka_minio_raw_topic
    consumer.subscribe([raw_topic])
    print(f"[minio-adapter] consuming raw MinIO notifications from {raw_topic}", flush=True)
    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"[minio-adapter] kafka error: {msg.error()}", file=sys.stderr, flush=True); continue
            try:
                payload = json.loads(msg.value().decode())
                # A MinIO notification may contain one or more records; route record-by-record by bucket.
                routed = 0
                for rec in payload.get("Records", []):
                    bucket = str(((rec.get("s3") or {}).get("bucket") or {}).get("name") or "")
                    group = _infer_group(db, bucket, raw_topic)
                    if not group:
                        raise ValueError(f"cannot uniquely route MinIO bucket {bucket!r} on raw topic {raw_topic!r} to an active AMP catalogue group")
                    normalized = events.normalize_s3_notification({"Records": [rec]}, group_id=group["id"],
                                                                  tenant_id=group["tenant_id"], source_type="MINIO_KAFKA")
                    for event in normalized:
                        events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
                        routed += 1
                consumer.commit(message=msg, asynchronous=False)
                print(f"[minio-adapter] normalized={routed}", flush=True)
            except Exception as exc:
                # Never silently consume a raw storage event that could not be normalized/routed.
                # First persist it to Kafka DLQ. Only after the DLQ publish succeeds do we commit
                # the raw-topic offset; otherwise leave the offset uncommitted for retry.
                print(f"[minio-adapter] FAILED offset={msg.offset()} error={exc}", file=sys.stderr, flush=True)
                try:
                    dlq_event = {
                        "schema_version": 1,
                        "source_type": "MINIO_RAW",
                        "raw_topic": raw_topic,
                        "partition": msg.partition(),
                        "offset": msg.offset(),
                        "payload": payload if 'payload' in locals() else {},
                    }
                    events.publish(
                        {"failed_event": dlq_event, "error": str(exc), "failed_at": now()},
                        bootstrap=settings.kafka_bootstrap,
                        topic=settings.kafka_dlq_topic,
                    )
                    consumer.commit(message=msg, asynchronous=False)
                    print(f"[minio-adapter] DLQ offset={msg.offset()} committed-after-dlq", file=sys.stderr, flush=True)
                except Exception as dlq_exc:
                    print(
                        f"[minio-adapter] DLQ publish failed offset={msg.offset()} error={dlq_exc}; offset NOT committed",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(2)
    finally:
        consumer.close()


def _enabled_configs(db: Database, mode: str) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """SELECT c.*,g.storage_id,g.container_name,g.container_type,s.endpoint,s.region,s.access_key,s.secret_key,s.options_json
           FROM change_capture_configs c JOIN catalogue_groups g ON g.id=c.catalogue_group_id
           JOIN storage_systems s ON s.id=g.storage_id WHERE c.enabled=? AND c.mode=?""",
        (True, mode),
    )
    for row in rows:
        row["config"] = db.loads(row.pop("config_json", "{}"), {})
        row["checkpoint"] = db.loads(row.pop("checkpoint_json", "{}"), {})
        row["options"] = db.loads(row.pop("options_json", "{}"), {})
    return rows


def poll_aws_sqs() -> None:
    db, cat, events = stack()
    sleep_seconds = int(os.getenv("AMP_AWS_SQS_POLL_INTERVAL", "5"))
    print("[aws-sqs-adapter] started", flush=True)
    while True:
        configs = _enabled_configs(db, "AWS_SQS")
        if not configs:
            time.sleep(sleep_seconds); continue
        for cfg in configs:
            qurl = cfg["config"].get("queue_url")
            if not qurl:
                continue
            client = boto3.client(
                "sqs", region_name=cfg.get("region") or "us-east-1",
                aws_access_key_id=cfg.get("access_key"), aws_secret_access_key=cfg.get("secret_key"),
                endpoint_url=cfg["config"].get("sqs_endpoint"),
                config=Config(retries={"max_attempts": 5}),
            )
            resp = client.receive_message(QueueUrl=qurl, MaxNumberOfMessages=10, WaitTimeSeconds=10,
                                          VisibilityTimeout=int(cfg["config"].get("visibility_timeout", 60)))
            for message in resp.get("Messages", []):
                try:
                    body = json.loads(message["Body"])
                    # SNS-wrapped messages are common; unwrap once.
                    if isinstance(body, dict) and isinstance(body.get("Message"), str):
                        body = json.loads(body["Message"])
                    if body.get("source") == "aws.s3" or "detail-type" in body:
                        normalized = events.normalize_eventbridge_s3(body, group_id=cfg["catalogue_group_id"], tenant_id=cfg["tenant_id"])
                    else:
                        normalized = events.normalize_s3_notification(body, group_id=cfg["catalogue_group_id"], tenant_id=cfg["tenant_id"], source_type="AWS_SQS")
                    for event in normalized:
                        events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
                    client.delete_message(QueueUrl=qurl, ReceiptHandle=message["ReceiptHandle"])
                    events.update_checkpoint(cfg["catalogue_group_id"], {"last_sqs_message_id": message.get("MessageId")}, status="HEALTHY")
                except Exception as exc:
                    events.update_checkpoint(cfg["catalogue_group_id"], cfg.get("checkpoint", {}), status="DEGRADED")
                    print(f"[aws-sqs-adapter] {cfg['catalogue_group_id']} error={exc}", file=sys.stderr, flush=True)
        time.sleep(1)


def poll_hcp_mqe() -> None:
    """Configurable HCP MQE change poller.

    Beta connector intentionally separates HCP query retrieval from normalization. Configure `query_url`,
    optional headers/query_body, `operations_path` (default `operations`) and `overlap_seconds`.
    The native response must be JSON or be translated by an HCP-side proxy to the canonical operation fields
    accepted by EventService.normalize_hcp_operations().
    """
    db, cat, events = stack()
    sleep_seconds = int(os.getenv("AMP_HCP_MQE_POLL_INTERVAL", "30"))
    client = httpx.Client(timeout=60.0)
    print("[hcp-mqe-adapter] started", flush=True)
    while True:
        configs = _enabled_configs(db, "HCP_MQE")
        if not configs:
            time.sleep(sleep_seconds); continue
        for cfg in configs:
            c = cfg["config"]
            url = c.get("query_url")
            if not url:
                continue
            checkpoint = cfg.get("checkpoint") or {}
            overlap = int(c.get("overlap_seconds", 300))
            previous = checkpoint.get("change_time")
            if previous:
                try:
                    since_dt = datetime.fromisoformat(previous.replace("Z", "+00:00")) - timedelta(seconds=overlap)
                except Exception:
                    since_dt = datetime.now(timezone.utc) - timedelta(seconds=overlap)
            else:
                since_dt = datetime.now(timezone.utc) - timedelta(seconds=int(c.get("initial_lookback_seconds", 3600)))
            until_dt = datetime.now(timezone.utc)
            replacements = {"{{since}}": since_dt.isoformat(), "{{until}}": until_dt.isoformat()}
            headers = dict(c.get("headers") or {})
            body = c.get("query_body") or {"since": "{{since}}", "until": "{{until}}"}
            raw = json.dumps(body)
            for k, v in replacements.items(): raw = raw.replace(k, v)
            try:
                resp = client.request(str(c.get("method", "POST")).upper(), url, headers=headers, json=json.loads(raw))
                resp.raise_for_status()
                payload = resp.json()
                operations = payload
                for part in str(c.get("operations_path", "operations")).split("."):
                    if part and isinstance(operations, dict): operations = operations.get(part, [])
                normalized = events.normalize_hcp_operations(operations or [], group_id=cfg["catalogue_group_id"], tenant_id=cfg["tenant_id"])
                for event in normalized:
                    events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
                events.update_checkpoint(cfg["catalogue_group_id"], {"change_time": until_dt.isoformat(), "events": len(normalized)}, status="HEALTHY")
            except Exception as exc:
                events.update_checkpoint(cfg["catalogue_group_id"], checkpoint, status="DEGRADED")
                print(f"[hcp-mqe-adapter] {cfg['catalogue_group_id']} error={exc}", file=sys.stderr, flush=True)
        time.sleep(sleep_seconds)


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "consume-normalized").lower()
    if mode == "consume-normalized": consume_normalized()
    elif mode == "consume-minio-raw": consume_minio_raw()
    elif mode == "aws-sqs-adapter": poll_aws_sqs()
    elif mode == "hcp-mqe-adapter": poll_hcp_mqe()
    else:
        print("usage: python -m app.event_worker [consume-normalized|consume-minio-raw|aws-sqs-adapter|hcp-mqe-adapter]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
