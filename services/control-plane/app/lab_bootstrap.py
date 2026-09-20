from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

AMP_URL = os.getenv("AMP_BOOTSTRAP_URL", "http://127.0.0.1:8080").rstrip("/")
TENANT = os.getenv("AMP_DEFAULT_TENANT", "demo")
PRIMARY_ENDPOINT = os.getenv("AMP_LAB_PRIMARY_ENDPOINT", "http://minio-primary:9000")
LEGACY_ENDPOINT = os.getenv("AMP_LAB_LEGACY_ENDPOINT", "http://minio-legacy:9000")
PRIMARY_BUCKET = os.getenv("AMP_LAB_PRIMARY_BUCKET", "amp-primary")
LEGACY_BUCKET = os.getenv("AMP_LAB_LEGACY_BUCKET", "legacy-hcp")
PRIMARY_ACCESS = os.getenv("AMP_LAB_PRIMARY_ACCESS_KEY", "ampprimary")
PRIMARY_SECRET = os.getenv("AMP_LAB_PRIMARY_SECRET_KEY", "ampprimary-change-me")
LEGACY_ACCESS = os.getenv("AMP_LAB_LEGACY_ACCESS_KEY", "amplegacy")
LEGACY_SECRET = os.getenv("AMP_LAB_LEGACY_SECRET_KEY", "amplegacy-change-me")

SAMPLES: dict[str, tuple[str, str]] = {
    "contracts/uk-master-services-agreement.txt": (
        "text/plain",
        "Master Services Agreement\nJurisdiction: UK\nThe customer may terminate for convenience "
        "with ninety days written notice. Data retention is seven years after termination.\n",
    ),
    "records/aus-retention-policy.json": (
        "application/json",
        json.dumps({"recordType": "CUSTOMER_RECORD", "jurisdiction": "AUS", "retentionYears": 7,
                    "classification": "CONFIDENTIAL"}, indent=2),
    ),
    "correspondence/payment-support.txt": (
        "text/plain",
        "Customer correspondence regarding mortgage payment difficulties. The customer requested a temporary "
        "payment arrangement and financial support review.\n",
    ),
    "archive/legacy-hcp-note.xml": (
        "application/xml",
        "<record><type>LEGAL</type><jurisdiction>UK</jurisdiction><owner>Finance</owner>"
        "<summary>Legacy HCP annotation-compatible record</summary></record>\n",
    ),
}


def s3_client(endpoint: str, access: str, secret: str):
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                        region_name="us-east-1", config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))


def wait_s3(client, label: str, attempts: int = 90) -> None:
    for i in range(attempts):
        try:
            client.list_buckets(); print(f"[ok] {label} is reachable"); return
        except Exception as exc:
            if i == attempts - 1:
                raise RuntimeError(f"{label} did not become ready: {exc}") from exc
            time.sleep(2)


def ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)
    print(f"[ok] bucket: {bucket}")


def ensure_bucket_versioning(client, bucket: str) -> None:
    try:
        client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status":"Enabled"})
        print(f"[ok] bucket versioning enabled: {bucket}")
    except Exception as exc:
        print(f"[warn] could not enable versioning for {bucket}: {exc}")


def ensure_bucket_events(client, bucket: str) -> None:
    """Attach MinIO bucket notifications to the Kafka target configured by MINIO_NOTIFY_KAFKA_*_AMP."""
    try:
        client.put_bucket_notification_configuration(
            Bucket=bucket,
            NotificationConfiguration={
                "QueueConfigurations": [{
                    "Id": "amp-catalogue-change-capture",
                    "QueueArn": "arn:minio:sqs::AMP:kafka",
                    "Events": ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"],
                }]
            },
        )
        print(f"[ok] bucket events -> Kafka: {bucket}")
    except Exception as exc:
        # Keep baseline lab usable even if a specific historical MinIO build handles target registration differently.
        print(f"[warn] could not configure MinIO bucket notification for {bucket}: {exc}")


def seed_legacy(client) -> None:
    for key, (content_type, body) in SAMPLES.items():
        try:
            client.head_object(Bucket=LEGACY_BUCKET, Key=key); continue
        except ClientError:
            pass
        client.put_object(Bucket=LEGACY_BUCKET, Key=key, Body=body.encode(), ContentType=content_type)
        print(f"[seed] {key}")


def wait_amp(attempts: int = 90) -> httpx.Client:
    client = httpx.Client(timeout=30.0)
    for i in range(attempts):
        try:
            r = client.get(f"{AMP_URL}/healthz")
            if r.is_success:
                print(f"[ok] AMP API ready at {AMP_URL}"); return client
        except Exception:
            pass
        if i == attempts - 1:
            raise RuntimeError(f"AMP did not become ready at {AMP_URL}")
        time.sleep(2)
    return client


def api(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    r = client.request(method, f"{AMP_URL}{path}", **kwargs)
    if not r.is_success:
        raise RuntimeError(f"{method} {path} failed: {r.status_code} {r.text}")
    return r.json() if r.content else None


def ensure_storage(client: httpx.Client, *, name: str, kind: str, role: str, endpoint: str,
                   access: str, secret: str) -> dict[str, Any]:
    rows = api(client, "GET", f"/api/v1/storage-systems?tenant_id={TENANT}")
    match = next((x for x in rows if x.get("name") == name), None)
    if match:
        print(f"[ok] storage registered: {name}"); return match
    body = {"tenant_id": TENANT, "name": name, "kind": kind, "role": role, "endpoint": endpoint,
            "region": "us-east-1", "access_key": access, "secret_key": secret,
            "options": {"lab": True, "addressing_style": "path"}}
    created = api(client, "POST", "/api/v1/storage-systems", json=body)
    print(f"[create] storage: {name} -> {created['id']}")
    return created


def ensure_catalogue(client: httpx.Client, *, storage: dict, bucket: str, name: str, physical_shards: int) -> dict[str, Any]:
    rows = api(client, "GET", f"/api/v1/catalogue-groups?tenant_id={TENANT}")
    match = next((x for x in rows if x.get("storage_id") == storage["id"] and x.get("container_name") == bucket), None)
    if match:
        print(f"[ok] catalogue registered: {name}"); return match
    body = {"tenant_id": TENANT, "storage_id": storage["id"], "name": name,
            "container_type": "S3_BUCKET", "container_name": bucket,
            "virtual_shards": 1024, "physical_shards": physical_shards}
    created = api(client, "POST", "/api/v1/catalogue-groups", json=body)
    print(f"[create] catalogue: {name} -> {created['id']} ({physical_shards} physical shards)")
    return created


def ensure_change_capture(client: httpx.Client, catalogue: dict, mode: str, config: dict | None = None) -> None:
    result = api(client, "POST", f"/api/v1/catalogue-groups/{catalogue['id']}/change-capture", json={
        "mode": mode, "enabled": True, "config": config or {}
    })
    print(f"[ok] change capture: {catalogue['name']} mode={result.get('mode')}")


def ensure_dataset(client: httpx.Client, legacy_storage: dict, legacy_bucket: str) -> None:
    rows = api(client, "GET", f"/api/v1/datasets?tenant_id={TENANT}")
    ds = next((x for x in rows if x.get("name") == "Enterprise Knowledge Lab"), None)
    if not ds:
        ds = api(client, "POST", "/api/v1/datasets", json={
            "tenant_id": TENANT, "name": "Enterprise Knowledge Lab",
            "description": "Search-index dataset populated independently from the AMP administrative catalogue.",
            "membership_mode": "DYNAMIC", "query": {"source_id": legacy_storage["id"], "container_name": legacy_bucket},
        })
        print(f"[create] dataset: {ds['id']}")
    api(client, "POST", f"/api/v1/datasets/{ds['id']}/materialize")
    print("[ok] dataset materialised")


def main() -> int:
    print("AMP Enterprise Beta lab bootstrap - container-sharded catalogue")
    primary = s3_client(PRIMARY_ENDPOINT, PRIMARY_ACCESS, PRIMARY_SECRET)
    legacy = s3_client(LEGACY_ENDPOINT, LEGACY_ACCESS, LEGACY_SECRET)
    wait_s3(primary, "primary S3")
    wait_s3(legacy, "legacy/HCP-S3 simulator")
    ensure_bucket(primary, PRIMARY_BUCKET)
    ensure_bucket(legacy, LEGACY_BUCKET)
    ensure_bucket_versioning(primary, PRIMARY_BUCKET)
    ensure_bucket_versioning(legacy, LEGACY_BUCKET)
    seed_legacy(legacy)
    ensure_bucket_events(primary, PRIMARY_BUCKET)
    ensure_bucket_events(legacy, LEGACY_BUCKET)

    client = wait_amp()
    primary_storage = ensure_storage(client, name="Lab Primary S3", kind="MINIO", role="PRIMARY",
                                     endpoint=PRIMARY_ENDPOINT, access=PRIMARY_ACCESS, secret=PRIMARY_SECRET)
    legacy_storage = ensure_storage(client, name="Legacy HCP-S3 Simulator", kind="HCP_S3", role="SECONDARY",
                                    endpoint=LEGACY_ENDPOINT, access=LEGACY_ACCESS, secret=LEGACY_SECRET)
    primary_cat = ensure_catalogue(client, storage=primary_storage, bucket=PRIMARY_BUCKET,
                                   name="Primary / amp-primary", physical_shards=2)
    legacy_cat = ensure_catalogue(client, storage=legacy_storage, bucket=LEGACY_BUCKET,
                                  name="Legacy / legacy-hcp", physical_shards=4)
    ensure_change_capture(client, primary_cat, "MINIO_KAFKA", {"raw_topic": "amp.raw.minio.primary"})
    ensure_change_capture(client, legacy_cat, "MINIO_KAFKA", {"raw_topic": "amp.raw.minio.legacy"})

    discovered = api(client, "POST", "/api/v1/discovery/run", json={
        "tenant_id": TENANT, "catalogue_group_id": legacy_cat["id"], "prefix": "", "auto_index": True,
    })
    print(f"[ok] catalogue generation={discovered.get('generation')} scanned={discovered.get('scanned')} registered={discovered.get('registered')}")
    ensure_dataset(client, legacy_storage, LEGACY_BUCKET)
    rec = api(client, "POST", "/api/v1/reconciliation/run", json={
        "tenant_id": TENANT, "catalogue_group_id": legacy_cat["id"], "target": "ALL",
    })
    print(f"[ok] reconciliation storage={rec['checked']['storage']} index={rec['checked']['index']} ai={rec['checked']['ai']} findings={rec.get('findings')}")
    ov = api(client, "GET", f"/api/v1/overview?tenant_id={TENANT}")
    print(f"[ready] catalogues={ov.get('catalogue_groups')} objects={ov.get('catalogue_objects')} indexed={ov.get('indexed')} ai_ready={ov.get('ai_ready')}")
    print(f"[ready] legacy catalogue={legacy_cat['id']} primary catalogue={primary_cat['id']}")
    print("Open AMP: http://localhost:8080")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise
