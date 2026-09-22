import os
import json
import tempfile
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ["AMP_DATABASE_URL"] = "sqlite:///" + str(Path(_tmp.name) / "amp-test.db")
os.environ["AMP_DATA_ROOT"] = str(Path(_tmp.name) / "data")
os.environ["AMP_DEMO_MODE"] = "false"
os.environ["AMP_TIKA_URL"] = ""

from app.db import Database
from app.services.catalog import CatalogService
from app.services.operations import OperationsService
from app.services.processing import ProcessingService


def stack(name="default"):
    db_path = Path(_tmp.name) / f"{name}.db"
    db = Database("sqlite:///" + str(db_path))
    db.init_schema()
    cat = CatalogService(db)
    ops = OperationsService(db, cat)
    proc = ProcessingService(db, cat)
    return db, cat, ops, proc


def local_group(cat, tenant, storage_name, root, role="EXTERNAL", physical_shards=4):
    s = cat.create_storage({"tenant_id": tenant, "name": storage_name, "kind": "LOCAL", "role": role, "root_path": str(root)})
    g = cat.create_catalogue_group(tenant=tenant, storage_id=s["id"], container_name=".", container_type="DIRECTORY",
                                   name=f"{storage_name} catalogue", physical_shards=physical_shards)
    return s, g


def test_catalogue_group_is_storage_plus_container_and_hop_is_independent():
    db, cat, ops, proc = stack("independent")
    root = Path(_tmp.name) / "independent-store"; root.mkdir(exist_ok=True)
    (root / "a.txt").write_text("termination for convenience is allowed")
    s, g = local_group(cat, "t1", "legacy", root)

    d = ops.discover("t1", g["id"])
    assert d["registered"] == 1 and d["generation"] == 1
    objs = cat.list_catalogue_objects(tenant="t1", group_id=g["id"])
    assert len(objs) == 1
    recon_id = objs[0]["recon_id"]

    # HOP simulator scans storage directly, not catalogue_objects.
    ix = proc.index_source(g["id"])
    assert ix["indexed"] == 1 and ix["catalogue_dependency"] is False
    idx = db.fetchone("SELECT recon_id FROM search_documents WHERE source_id=? AND container_name=?", (s["id"], "."))
    assert idx["recon_id"] == recon_id
    hits = proc.search("t1", "termination convenience")
    assert hits and hits[0]["recon_id"] == recon_id

    rec = ops.reconcile("t1", g["id"], "ALL")
    assert rec["findings"] == 0


def test_migration_creates_independent_source_and_target_catalogues():
    db, cat, ops, proc = stack("migration")
    src_root = Path(_tmp.name) / "mig-src"; tgt_root = Path(_tmp.name) / "mig-tgt"
    src_root.mkdir(exist_ok=True); tgt_root.mkdir(exist_ok=True)
    (src_root / "x.txt").write_text("hello migration")
    _, sg = local_group(cat, "t2", "source", src_root, "SECONDARY", 4)
    _, tg = local_group(cat, "t2", "target", tgt_root, "PRIMARY", 2)
    ops.discover("t2", sg["id"])
    r = ops.migrate("t2", sg["id"], tg["id"])
    assert r["copied"] == 1
    src = cat.list_catalogue_objects(tenant="t2", group_id=sg["id"])
    tgt = cat.list_catalogue_objects(tenant="t2", group_id=tg["id"])
    assert len(src) == 1 and len(tgt) == 1
    assert src[0]["recon_id"] != tgt[0]["recon_id"]
    assert tgt[0]["origin_recon_id"] == src[0]["recon_id"]
    assert db.scalar("SELECT COUNT(*) c FROM migration_links", (), 0) == 1
    cat.set_group_state(sg["id"], "ARCHIVED")
    assert cat.get_catalogue_group(tg["id"])["state"] == "ACTIVE"


def test_hydration_registers_target_only_after_successful_write():
    db, cat, ops, proc = stack("hydrate")
    src_root = Path(_tmp.name) / "hyd-src"; tgt_root = Path(_tmp.name) / "hyd-tgt"
    src_root.mkdir(exist_ok=True); tgt_root.mkdir(exist_ok=True)
    (src_root / "legacy.txt").write_text("legacy payload")
    _, sg = local_group(cat, "t3", "legacy", src_root, "SECONDARY")
    _, tg = local_group(cat, "t3", "primary", tgt_root, "PRIMARY")
    ops.discover("t3", sg["id"])
    source_obj = cat.list_catalogue_objects(tenant="t3", group_id=sg["id"])[0]
    assert cat.list_catalogue_objects(tenant="t3", group_id=tg["id"]) == []
    data, _ = proc.read_catalogue_object(source_obj["id"], tg["id"])
    assert data == b"legacy payload"
    target_obj = cat.list_catalogue_objects(tenant="t3", group_id=tg["id"])[0]
    assert target_obj["storage_layout"] == "AMP_PACKAGE_V3"
    assert (tgt_root / target_obj["payload_key"]).read_bytes() == b"legacy payload"
    assert target_obj["origin_recon_id"] == source_obj["recon_id"]


def test_generations_create_missing_then_tombstone():
    db, cat, ops, proc = stack("tombstone")
    root = Path(_tmp.name) / "tomb-store"; root.mkdir(exist_ok=True)
    p = root / "gone.txt"; p.write_text("temporary")
    _, g = local_group(cat, "t4", "archive", root)
    ops.discover("t4", g["id"])
    p.unlink()
    ops.discover("t4", g["id"])
    o = cat.list_catalogue_objects(tenant="t4", group_id=g["id"])[0]
    assert o["lifecycle_state"] == "MISSING" and o["missing_count"] == 1
    ops.discover("t4", g["id"])
    o = cat.list_catalogue_objects(tenant="t4", group_id=g["id"])[0]
    assert o["lifecycle_state"] == "TOMBSTONED" and o["missing_count"] == 2


def test_virtual_shard_routing_is_stable_and_bounded():
    db, cat, ops, proc = stack("shards")
    root = Path(_tmp.name) / "shard-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t5", "huge", root, physical_shards=8)
    shards = cat.list_shards(g["id"])
    assert len(shards) == 8
    assert shards[0]["virtual_start"] == 0 and shards[-1]["virtual_end"] == 1023
    rid1 = cat.recon_id_for(g, "records/a.json", "v1")
    rid2 = cat.recon_id_for(g, "records/a.json", "v1")
    assert rid1 == rid2
    vs = cat.virtual_shard_for(rid1, 1024)
    shard = cat.shard_for_virtual(g["id"], vs)
    assert shard["virtual_start"] <= vs <= shard["virtual_end"]


def test_event_change_capture_inserts_updates_tombstones_and_deduplicates():
    from app.services.events import EventService
    db, cat, ops, proc = stack("events")
    events = EventService(db, cat)
    root = Path(_tmp.name) / "event-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t6", "event-source", root)
    events.configure_capture(g["id"], "GENERIC_KAFKA", {"topic": "amp.storage.changes"})

    # Object appears outside AMP: event triggers a targeted HEAD + catalogue upsert without a full discovery.
    p = root / "outside.txt"; p.write_text("created outside AMP")
    event = {
        "tenant_id": "t6", "catalogue_group_id": g["id"], "source_type": "TEST_KAFKA",
        "event_type": "OBJECT_CREATED", "object_key": "outside.txt", "version_id": "",
        "native_event_id": "evt-1", "event_time": "2026-09-17T12:00:00+00:00", "sequencer": "1",
    }
    r = events.apply_event(event)
    assert r["status"] == "APPLIED"
    obj = cat.find_catalogue_object(g["id"], "outside.txt")
    assert obj and obj["source_mode"] == "EVENT" and obj["lifecycle_state"] == "ACTIVE"

    # Duplicate at-least-once delivery is ignored safely.
    dup = events.apply_event(event)
    assert dup["status"] == "DUPLICATE" and dup["applied"] is False
    assert db.scalar("SELECT COUNT(*) c FROM storage_events", (), 0) == 1

    # External update refreshes current storage state, not event payload guesses.
    p.write_text("updated outside AMP with more content")
    update = dict(event, native_event_id="evt-2", event_type="OBJECT_UPDATED", sequencer="2")
    events.apply_event(update)
    updated = cat.find_catalogue_object(g["id"], "outside.txt")
    assert updated["size_bytes"] == p.stat().st_size and updated["lifecycle_state"] == "ACTIVE"

    # A confirmed delete event creates a tombstone immediately for downstream recon.
    p.unlink()
    delete = dict(event, native_event_id="evt-3", event_type="OBJECT_DELETED", sequencer="3")
    events.apply_event(delete)
    deleted = cat.find_catalogue_object(g["id"], "outside.txt")
    assert deleted["lifecycle_state"] == "TOMBSTONED" and deleted["missing_count"] == 2
    capture = events.get_capture(g["id"])
    assert capture["status"] == "HEALTHY" and capture["last_applied_at"]


def test_minio_s3_notification_normalizes_to_amp_event_schema():
    from app.services.events import EventService
    db, cat, ops, proc = stack("normalize")
    events = EventService(db, cat)
    root = Path(_tmp.name) / "normalize-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t7", "minio", root)
    payload = {
        "Records": [{
            "eventName": "s3:ObjectCreated:Put",
            "eventTime": "2026-09-17T12:00:00.000Z",
            "eventID": "minio-event-1",
            "s3": {"bucket": {"name": "."}, "object": {"key": "a%20b.txt", "size": 12, "eTag": "etag1", "sequencer": "ABC"}}
        }]
    }
    out = events.normalize_s3_notification(payload, group_id=g["id"], tenant_id="t7", source_type="MINIO_KAFKA")
    assert len(out) == 1
    assert out[0]["schema_version"] == 1 and out[0]["event_type"] == "OBJECT_CREATED"
    assert out[0]["object_key"] == "a b.txt" and out[0]["native_event_id"] == "minio-event-1"


def test_event_sequencer_rejects_stale_delivery():
    from app.services.events import EventService
    db, cat, ops, proc = stack("event-order")
    events = EventService(db, cat)
    root = Path(_tmp.name) / "event-order-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t8", "ordered", root)
    events.configure_capture(g["id"], "GENERIC_KAFKA", {"topic": "amp.storage.changes"})
    p = root / "ordered.txt"; p.write_text("new")
    newer = {"tenant_id":"t8","catalogue_group_id":g["id"],"source_type":"AWS_SQS","event_type":"OBJECT_CREATED",
             "object_key":"ordered.txt","native_event_id":"newer","event_time":"2026-09-17T12:01:00+00:00","sequencer":"0A"}
    assert events.apply_event(newer)["status"] == "APPLIED"
    stale = dict(newer, native_event_id="older", event_time="2026-09-17T12:00:00+00:00", sequencer="09")
    result = events.apply_event(stale)
    assert result["status"] == "IGNORED_STALE" and result["applied"] is False


def test_catalogue_scheduler_configuration_and_run():
    from app.services.scheduler import ScheduleService
    db, cat, ops, proc = stack("scheduler")
    root = Path(_tmp.name) / "schedule-store"; root.mkdir(exist_ok=True)
    (root / "a.txt").write_text("scheduled discovery")
    _, g = local_group(cat, "t9", "scheduled", root)
    svc = ScheduleService(db, cat, ops)
    row = svc.configure(g["id"], "BASELINE", 60, enabled=True)
    assert row["schedule_type"] == "BASELINE" and int(row["interval_minutes"]) == 60
    result = svc.run(row)
    assert result["result"]["scanned"] == 1
    assert len(cat.list_catalogue_objects(tenant="t9", group_id=g["id"])) == 1


def test_tombstoned_catalogue_object_does_not_attempt_source_read():
    db, cat, ops, proc = stack("tombstone-payload")
    root = Path(_tmp.name) / "tomb-payload-store"; root.mkdir(exist_ok=True)
    p = root / "gone.txt"; p.write_text("payload")
    _, g = local_group(cat, "t10", "tomb-payload", root)
    ops.discover("t10", g["id"])
    obj = cat.list_catalogue_objects(tenant="t10", group_id=g["id"])[0]
    p.unlink()
    cat.tombstone_by_key(g["id"], "gone.txt", source_mode="EVENT")
    try:
        proc.read_catalogue_object(obj["id"])
        assert False, "expected FileNotFoundError for tombstoned payload"
    except FileNotFoundError:
        pass


def test_hcp_rest_gateway_writes_payload_and_three_annotation_sidecars():
    from fastapi.testclient import TestClient
    from app.main import app, catalog as app_catalog, db as app_db

    app_db.init_schema()
    root = Path(_tmp.name) / "hcp-rest-store"
    root.mkdir(exist_ok=True)
    storage = app_catalog.create_storage({
        "tenant_id": "t11", "name": "hcp-rest-target", "kind": "LOCAL",
        "role": "PRIMARY", "root_path": str(root),
    })
    group = app_catalog.create_catalogue_group(
        tenant="t11", storage_id=storage["id"], container_name=".",
        container_type="DIRECTORY", name="HCP REST target", physical_shards=2,
    )
    app_catalog.configure_gateway_route(
        tenant="t11", namespace="legal", catalogue_group_id=group["id"]
    )

    client = TestClient(app)
    object_path = "contracts/test-object-01.json"
    payload = b'{"id":"test-object-01","title":"HCP REST object"}'
    r = client.put(f"/rest/t11/legal/{object_path}", content=payload,
                   headers={"content-type": "application/json"})
    assert r.status_code == 201
    object_id = r.headers["x-amp-object-id"]
    recon_id = r.headers["x-amp-recon-id"]

    annotations = {
        "default": b'{"recordType":"CONTRACT","jurisdiction":"UK"}',
        "legal": b'{"retentionYears":10,"legalHold":false}',
        "migration": b'{"sourceSystem":"HCP-LAB","wave":"wave-01"}',
    }
    for name, body in annotations.items():
        ar = client.put(
            f"/rest/t11/legal/{object_path}?type=custom-metadata&annotation={name}",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert ar.status_code == 201
        assert ar.headers["x-amp-recon-id"] == recon_id
        assert ar.headers["x-hcp-annotation"] == name
        assert ar.headers["x-amp-sidecar-key"].startswith(".amp/objects/")
        assert ar.headers["x-amp-sidecar-key"].endswith(f"/annotations/{name}.json")

    detail = client.get(f"/api/v1/catalogue-objects/{object_id}").json()
    assert detail["source_mode"] == "GATEWAY"
    assert detail["storage_layout"] == "AMP_PACKAGE_V3"
    parts = detail["package_root"].split("/")
    assert parts[:2] == [".amp", "objects"]
    assert len(parts) == 5
    assert len(parts[2]) == 2 and len(parts[3]) == 2
    assert detail["payload_key"] == f"{detail['package_root']}/payload"
    assert detail["manifest_key"] == f"{detail['package_root']}/manifest.json"
    assert (root / detail["payload_key"]).read_bytes() == payload
    assert sorted(a["annotation_name"] for a in detail["annotations"]) == ["default", "legal", "migration"]
    for name in annotations:
        assert (root / detail["package_root"] / "annotations" / f"{name}.json").exists()
    manifest = json.loads((root / detail["manifest_key"]).read_text())
    assert manifest["logicalPath"] == object_path
    assert manifest["payload"]["key"] == detail["payload_key"]
    assert sorted(a["name"] for a in manifest["annotations"]) == ["default", "legal", "migration"]

    got = client.get(f"/rest/t11/legal/{object_path}")
    assert got.status_code == 200 and got.content == payload
    for name, body in annotations.items():
        got_ann = client.get(f"/rest/t11/legal/{object_path}?type=custom-metadata&annotation={name}")
        assert got_ann.status_code == 200 and got_ann.content == body

    # Reserved sidecars are never business catalogue rows.
    assert app_catalog.list_catalogue_objects(tenant="t11", group_id=group["id"], q=".amp/", limit=100) == []


def test_discovery_reconstructs_amp_package_without_cataloguing_members():
    import json as _json
    db, cat, ops, proc = stack("package-rebuild")
    root = Path(_tmp.name) / "package-rebuild-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t12", "package-store", root)
    logical = "contracts/rebuilt.pdf"
    (root / logical / "annotations").mkdir(parents=True, exist_ok=True)
    (root / logical / ".amp").mkdir(parents=True, exist_ok=True)
    (root / logical / "payload").write_bytes(b"rebuilt payload")
    (root / logical / "annotations" / "legal.json").write_text('{"retentionYears":10}')
    manifest = {
        "manifestVersion": 1,
        "storageLayout": "AMP_PACKAGE_V1",
        "objectId": "storage-only",
        "reconId": "storage-only",
        "logicalPath": logical,
        "packageRoot": logical,
        "payload": {"key": f"{logical}/payload", "contentType": "application/pdf", "versionId": ""},
        "annotations": [{"name": "legal", "key": f"{logical}/annotations/legal.json"}],
    }
    (root / logical / ".amp" / "manifest.json").write_text(_json.dumps(manifest))

    result = ops.discover("t12", g["id"])
    assert result["registered"] == 1
    rows = cat.list_catalogue_objects(tenant="t12", group_id=g["id"], limit=100)
    assert len(rows) == 1
    assert rows[0]["object_key"] == logical
    assert rows[0]["storage_layout"] == "AMP_PACKAGE_V1"
    assert rows[0]["payload_key"] == f"{logical}/payload"



def test_s3_and_hcp_protocols_share_canonical_amp_package_v3():
    from fastapi.testclient import TestClient
    from app.main import app, catalog as app_catalog, db as app_db

    app_db.init_schema()
    root = Path(_tmp.name) / "s3-interop-store"
    root.mkdir(exist_ok=True)
    tenant = "demo"  # S3 beta front door maps configured credentials to the default lab tenant.
    bucket = "interop-unit"
    storage = app_catalog.create_storage({
        "tenant_id": tenant, "name": "interop-target", "kind": "LOCAL",
        "role": "PRIMARY", "root_path": str(root),
    })
    group = app_catalog.create_catalogue_group(
        tenant=tenant, storage_id=storage["id"], container_name=".",
        container_type="DIRECTORY", name="Interop target", physical_shards=2,
    )
    app_catalog.configure_gateway_route(tenant=tenant, namespace=bucket, catalogue_group_id=group["id"])

    client = TestClient(app)

    # HCP REST write -> S3 read.
    hcp_key = "contracts/hcp-created.txt"
    hcp_payload = b"created through HCP REST, read through S3"
    r = client.put(f"/rest/demo/interop-unit/{hcp_key}", content=hcp_payload, headers={"content-type": "text/plain"})
    assert r.status_code == 201
    hcp_obj = app_catalog.find_current_catalogue_object(group["id"], hcp_key)
    assert hcp_obj and hcp_obj["storage_layout"] == "AMP_PACKAGE_V3"
    s3_get = client.get(f"/{bucket}/{hcp_key}")
    assert s3_get.status_code == 200 and s3_get.content == hcp_payload

    # S3 write -> HCP REST read, with metadata/tags represented as sidecars.
    s3_key = "contracts/s3-created.json"
    s3_payload = b'{"title":"S3-created AMP object"}'
    put = client.put(
        f"/{bucket}/{s3_key}", content=s3_payload,
        headers={
            "content-type": "application/json",
            "x-amz-meta-jurisdiction": "UK",
            "x-amz-meta-owner": "Legal",
            "x-amz-tagging": "recordType=CONTRACT&classification=CONFIDENTIAL",
        },
    )
    assert put.status_code == 200
    s3_obj = app_catalog.find_current_catalogue_object(group["id"], s3_key)
    assert s3_obj and s3_obj["storage_layout"] == "AMP_PACKAGE_V3"
    pr = s3_obj["package_root"].split("/")
    assert pr[:2] == [".amp", "objects"] and len(pr) == 5
    assert (root / s3_obj["payload_key"]).read_bytes() == s3_payload
    anns = {a["annotation_name"] for a in app_catalog.list_annotations(s3_obj["id"])}
    assert {"s3-metadata", "s3-tags"}.issubset(anns)

    hcp_get = client.get(f"/rest/demo/interop-unit/{s3_key}")
    assert hcp_get.status_code == 200 and hcp_get.content == s3_payload

    head = client.head(f"/{bucket}/{s3_key}")
    assert head.status_code == 200
    assert head.headers["x-amz-meta-jurisdiction"] == "UK"
    assert head.headers["x-amz-meta-owner"] == "Legal"

    tags = client.get(f"/{bucket}/{s3_key}?tagging")
    assert tags.status_code == 200 and "CONTRACT" in tags.text and "CONFIDENTIAL" in tags.text

    # Range GET is part of the S3 P0 compatibility subset.
    partial = client.get(f"/{bucket}/{s3_key}", headers={"range": "bytes=0-4"})
    assert partial.status_code == 206 and partial.content == s3_payload[:5]

    # Logical listing hides all reserved physical package members.
    listing = client.get(f"/{bucket}?list-type=2&prefix=contracts/")
    assert listing.status_code == 200
    assert hcp_key in listing.text and s3_key in listing.text
    assert ".amp/objects/" not in listing.text

    physical = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    assert any(x.startswith(".amp/objects/") and x.endswith("/manifest.json") for x in physical)
    assert hcp_key not in physical and s3_key not in physical


def test_amp_package_v3_client_path_placement_preserves_package_interior():
    from fastapi.testclient import TestClient
    from app.main import app, catalog as app_catalog, db as app_db

    app_db.init_schema()
    root = Path(_tmp.name) / "client-path-store"
    root.mkdir(exist_ok=True)
    tenant = "t15"
    storage = app_catalog.create_storage({
        "tenant_id": tenant, "name": "client-path-target", "kind": "LOCAL",
        "role": "PRIMARY", "root_path": str(root),
    })
    group = app_catalog.create_catalogue_group(
        tenant=tenant, storage_id=storage["id"], container_name=".",
        container_type="DIRECTORY", name="Client path target", physical_shards=2,
        package_placement_mode="CLIENT_PATH",
    )
    app_catalog.configure_gateway_route(tenant=tenant, namespace="legal", catalogue_group_id=group["id"])
    client = TestClient(app)
    logical = "contracts/2026/agreement.pdf"
    payload = b"client-path-package"
    r = client.put(f"/rest/{tenant}/legal/{logical}", content=payload, headers={"content-type":"application/pdf"})
    assert r.status_code == 201
    oid = r.headers["x-amp-object-id"]
    d = client.get(f"/api/v1/catalogue-objects/{oid}").json()
    assert d["storage_layout"] == "AMP_PACKAGE_V3"
    assert d["package_root"].startswith(logical + "/")
    package_id = d["package_root"].rsplit("/", 1)[-1]
    assert len(package_id) == 36
    assert d["payload_key"] == f"{d['package_root']}/payload"
    assert d["manifest_key"] == f"{d['package_root']}/manifest.json"
    assert (root / d["payload_key"]).read_bytes() == payload
    ar = client.put(f"/rest/{tenant}/legal/{logical}?type=custom-metadata&annotation=legal",
                    content=b'{"retentionYears":10}', headers={"content-type":"application/json"})
    assert ar.status_code == 201
    assert ar.headers["x-amp-sidecar-key"] == f"{d['package_root']}/annotations/legal.json"
    manifest = json.loads((root / d["manifest_key"]).read_text())
    assert manifest["placementMode"] == "CLIENT_PATH"
    assert manifest["packageId"] == package_id
    assert manifest["logicalPath"] == logical


def test_discovery_reconstructs_amp_package_v2_from_reserved_manifest():
    db, cat, ops, proc = stack("package-v2-rebuild")
    root = Path(_tmp.name) / "package-v2-rebuild-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t14", "package-v2-store", root)
    logical = "contracts/v2-rebuilt.pdf"
    pid = "11111111-2222-3333-4444-555555555555"
    package_root = f".amp/objects/{pid}"
    (root / package_root / "annotations").mkdir(parents=True, exist_ok=True)
    (root / package_root / "payload").write_bytes(b"v2 rebuilt payload")
    (root / package_root / "annotations" / "legal.json").write_text('{"retentionYears":10}')
    manifest = {
        "manifestVersion": 2,
        "storageLayout": "AMP_PACKAGE_V2",
        "logicalPath": logical,
        "packageRoot": package_root,
        "payload": {"key": f"{package_root}/payload", "contentType": "application/pdf", "versionId": ""},
        "annotations": [{"name": "legal", "key": f"{package_root}/annotations/legal.json"}],
    }
    (root / package_root / "manifest.json").write_text(json.dumps(manifest))

    result = ops.discover("t14", g["id"])
    assert result["registered"] == 1
    rows = cat.list_catalogue_objects(tenant="t14", group_id=g["id"], limit=100)
    assert len(rows) == 1
    assert rows[0]["object_key"] == logical
    assert rows[0]["storage_layout"] == "AMP_PACKAGE_V2"
    assert rows[0]["payload_key"] == f"{package_root}/payload"


def test_backend_native_versions_are_catalogued_without_new_logical_object():
    db, cat, ops, proc = stack("native-versions")
    root = Path(_tmp.name) / "native-version-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t16", "versioned", root)
    first = cat.upsert_catalogue_object(group_id=g["id"], object_key="obj1", version_id="v1",
        size_bytes=10, etag="e1", checksum="c1", content_type="text/plain", source_mode="GATEWAY",
        metadata={"backend_response":{"VersionId":"v1"}})
    second = cat.upsert_catalogue_object(group_id=g["id"], object_key="obj1", version_id="v2",
        size_bytes=12, etag="e2", checksum="c2", content_type="text/plain", source_mode="GATEWAY",
        metadata={"backend_response":{"VersionId":"v2"}})
    rows = cat.list_catalogue_objects(tenant="t16", group_id=g["id"])
    assert len(rows) == 1
    assert first["id"] == second["id"] and first["recon_id"] == second["recon_id"]
    detail = cat.get_catalogue_object(first["id"])
    assert detail["version_id"] == "v2"
    assert [v["native_version_id"] for v in detail["native_versions"]] == ["v2", "v1"]
    assert sum(1 for v in detail["native_versions"] if v["is_current"]) == 1


def test_gateway_response_policy_is_configurable_per_route():
    db, cat, ops, proc = stack("response-policy")
    root = Path(_tmp.name) / "response-policy-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t17", "policy", root)
    route = cat.configure_gateway_route(tenant="t17", namespace="legal", catalogue_group_id=g["id"],
        response_mode="RAW_BACKEND", backend_header_policy="ALL_SAFE", add_amp_request_id=True,
        capture_backend_response=True, max_captured_error_body_bytes=32768)
    assert route["response_mode"] == "RAW_BACKEND"
    assert route["backend_header_policy"] == "ALL_SAFE"
    assert bool(route["capture_backend_response"])
    from app.services.gateway_response import normalized_error, filter_headers
    assert normalized_error(404, "r1")["error"] == "HTTP_STATUS_404"
    headers = filter_headers({"ETag":"abc","Connection":"close","Server":"secret"}, "ALL_SAFE")
    assert headers == {"etag":"abc"}


def test_gateway_response_modes_normalize_or_preserve_backend_outcome():
    from fastapi.testclient import TestClient
    from app.main import app, catalog as app_catalog, db as app_db
    app_db.init_schema()
    root = Path(_tmp.name) / "response-mode-live"; root.mkdir(exist_ok=True)
    tenant = "t18"; ns = "legal"
    storage = app_catalog.create_storage({"tenant_id":tenant,"name":"response-mode-target","kind":"LOCAL","role":"PRIMARY","root_path":str(root)})
    group = app_catalog.create_catalogue_group(tenant=tenant, storage_id=storage["id"], container_name=".", container_type="DIRECTORY", name="Response mode target")
    client = TestClient(app)
    app_catalog.configure_gateway_route(tenant=tenant, namespace=ns, catalogue_group_id=group["id"], response_mode="AMP_NORMALIZED")
    r = client.get(f"/rest/{tenant}/{ns}/missing.txt")
    assert r.status_code == 404 and r.json()["error"] == "HTTP_STATUS_404"
    app_catalog.configure_gateway_route(tenant=tenant, namespace=ns, catalogue_group_id=group["id"], response_mode="AMP_NORMALIZED_WITH_BACKEND")
    r = client.get(f"/rest/{tenant}/{ns}/missing.txt")
    assert r.status_code == 404 and r.json()["backend"]["status"] == 404
    app_catalog.configure_gateway_route(tenant=tenant, namespace=ns, catalogue_group_id=group["id"], response_mode="RAW_BACKEND", backend_header_policy="ALL_SAFE")
    r = client.get(f"/rest/{tenant}/{ns}/missing.txt")
    assert r.status_code == 404 and "object not found" in r.text
    tx = app_catalog.list_backend_transactions(tenant, 20)
    assert len([x for x in tx if x["outcome"] == "FAILED"]) >= 3


def test_payload_versions_link_to_annotation_snapshots_without_amp_version_keys():
    db, cat, ops, proc = stack("version-annotation-links")
    root = Path(_tmp.name) / "version-annotation-links-store"; root.mkdir(exist_ok=True)
    _, g = local_group(cat, "t19", "version-links", root)
    obj = cat.upsert_catalogue_object(group_id=g["id"], object_key="obj1", version_id="p1",
        size_bytes=10, etag="e1", checksum="c1", content_type="text/plain", source_mode="GATEWAY")
    ann = cat.upsert_annotation(obj["id"], "legal", ".amp/objects/a/b/id/annotations/legal.json",
        "application/json", 10, "a1hash", "a1", {"VersionId":"a1"})
    assert cat.annotation_version_for_payload(obj["id"], "legal", "p1") == "a1"

    # A new backend payload version reuses the same logical object and initially inherits
    # the currently observed annotation version; AMP records a link, not a copied payload/sidecar.
    obj2 = cat.upsert_catalogue_object(group_id=g["id"], object_key="obj1", version_id="p2",
        size_bytes=12, etag="e2", checksum="c2", content_type="text/plain", source_mode="GATEWAY")
    cat.link_annotations_to_payload_version(obj2["id"], "p2")
    assert obj2["id"] == obj["id"]
    assert cat.annotation_version_for_payload(obj["id"], "legal", "p2") == "a1"

    # Updating only the annotation lets the backend create its native sidecar version and
    # moves only the p2 snapshot link; p1 remains mapped to the historical annotation.
    cat.upsert_annotation(obj["id"], "legal", ".amp/objects/a/b/id/annotations/legal.json",
        "application/json", 11, "a2hash", "a2", {"VersionId":"a2"})
    assert cat.annotation_version_for_payload(obj["id"], "legal", "p1") == "a1"
    assert cat.annotation_version_for_payload(obj["id"], "legal", "p2") == "a2"
    links = cat.list_version_annotation_links(obj["id"])
    assert {(x["payload_native_version_id"], x["annotation_native_version_id"]) for x in links} == {("p1","a1"),("p2","a2")}



def test_bodyless_success_headers_do_not_leak_content_length():
    from app.services.gateway_response import sanitize_success_headers
    backend_headers = {
        "content-length": "166",
        "content-type": "application/json",
        "etag": "abc",
        "x-amz-version-id": "v1",
    }
    put_headers = sanitize_success_headers(backend_headers, method="PUT", has_body=False)
    assert "content-length" not in put_headers
    assert "content-type" not in put_headers
    assert put_headers["etag"] == "abc"
    assert put_headers["x-amz-version-id"] == "v1"
    head_headers = sanitize_success_headers(backend_headers, method="HEAD", has_body=False)
    assert head_headers["content-length"] == "166"


def test_bodyful_success_headers_override_backend_content_length():
    from app.services.gateway_response import sanitize_success_headers
    backend_headers = {
        "content-length": "61",
        "content-type": "application/octet-stream",
        "etag": "abc",
        "x-amz-checksum-crc32": "A2v8Xg==",
        "x-amz-checksum-sha256": "ZmFrZS1mdWxsLW9iamVjdC1zaGEyNTY=",
        "content-md5": "ZmFrZQ==",
    }
    range_headers = sanitize_success_headers(
        {**backend_headers, "content-range": "bytes 0-4/61"},
        method="GET", has_body=True, body_length=5,
    )
    assert range_headers["content-length"] == "5"
    assert range_headers["content-range"] == "bytes 0-4/61"
    assert "x-amz-checksum-crc32" not in range_headers
    assert "x-amz-checksum-sha256" not in range_headers
    assert "content-md5" not in range_headers
    full_headers = sanitize_success_headers(
        backend_headers, method="GET", has_body=True, body_length=61,
    )
    assert full_headers["content-length"] == "61"
    assert full_headers["x-amz-checksum-crc32"] == "A2v8Xg=="


def test_error_headers_do_not_leak_backend_body_framing_or_checksums():
    from app.services.gateway_response import sanitize_error_headers
    headers = sanitize_error_headers({
        "Content-Length": "355",
        "Content-Type": "application/xml",
        "Content-Encoding": "gzip",
        "x-amz-checksum-crc32": "A2v8Xg==",
        "Content-MD5": "ZmFrZQ==",
        "x-amz-request-id": "backend-123",
        "Retry-After": "2",
    })
    assert "content-length" not in headers
    assert "content-type" not in headers
    assert "content-encoding" not in headers
    assert "x-amz-checksum-crc32" not in headers
    assert "content-md5" not in headers
    assert headers["x-amz-request-id"] == "backend-123"
    assert headers["retry-after"] == "2"
