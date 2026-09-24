from __future__ import annotations
import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .db import Database
from .services.catalog import CatalogService, now, uid
from .services.storage import backend_from_record
from .services.processing import ProcessingService
from .services.operations import OperationsService
from .services.events import EventService
from .services.scheduler import ScheduleService
from .services.enterprise import EnterpriseService

app = FastAPI(title="AMP Enterprise Beta API", version=settings.version, docs_url="/docs", redoc_url="/redoc")
db = Database(settings.database_url)
catalog = CatalogService(db)
processing = ProcessingService(db, catalog)
ops = OperationsService(db, catalog)
events = EventService(db, catalog)
schedules = ScheduleService(db, catalog, ops)
enterprise = EnterpriseService(db)
STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    if settings.api_key and (request.url.path.startswith("/api/") or request.url.path.startswith("/rest/")) and request.headers.get("x-api-key") != settings.api_key:
        return Response(content='{"detail":"unauthorized"}', status_code=401, media_type="application/json", headers={"x-request-id": rid})
    resp = await call_next(request)
    resp.headers["x-request-id"] = rid
    resp.headers["x-content-type-options"] = "nosniff"
    resp.headers["referrer-policy"] = "same-origin"
    # Beta UI assets change between builds; do not let browsers execute stale JS
    # against a newer API contract. Production builds should use content-hashed assets.
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["pragma"] = "no-cache"
        resp.headers["expires"] = "0"
    return resp


@app.on_event("startup")
def startup():
    db.init_schema()
    enterprise.init_schema()
    if settings.demo_mode:
        seed_demo()


@app.get("/")
def root():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def health():
    return {"status": "ok", "version": settings.version, "catalogue_model": "container-sharded-v6-backend-authoritative", "event_backbone": bool(settings.kafka_bootstrap)}


@app.get("/readyz")
def ready():
    db.scalar("SELECT 1 ok")
    return {"status": "ready"}


class StorageIn(BaseModel):
    tenant_id: str = settings.default_tenant
    name: str
    kind: str = "LOCAL"
    role: str = "EXTERNAL"
    endpoint: str | None = None
    root_path: str | None = None
    region: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    options: dict = Field(default_factory=dict)


class CatalogueIn(BaseModel):
    tenant_id: str = settings.default_tenant
    storage_id: str
    name: str | None = None
    container_type: str = "S3_BUCKET"
    container_name: str
    virtual_shards: int = 1024
    physical_shards: int = 1


class CatalogueStateIn(BaseModel):
    state: str


class DiscoverIn(BaseModel):
    tenant_id: str = settings.default_tenant
    catalogue_group_id: str
    prefix: str = ""
    auto_index: bool = False


class ReconcileIn(BaseModel):
    tenant_id: str = settings.default_tenant
    catalogue_group_id: str
    target: str = "ALL"
    shard_id: str | None = None
    prefix: str = ""
    storage_mode: str = "TARGETED"
    verify_package_members: bool = True
    limit: int = 10000


class DatasetIn(BaseModel):
    tenant_id: str = settings.default_tenant
    name: str
    description: str = ""
    membership_mode: str = "DYNAMIC"
    query: dict = Field(default_factory=dict)


class RetrieveIn(BaseModel):
    tenant_id: str = settings.default_tenant
    query: str
    dataset_id: str | None = None
    top_k: int = 10


class ChangeCaptureIn(BaseModel):
    mode: str
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class StorageEventIn(BaseModel):
    tenant_id: str = settings.default_tenant
    catalogue_group_id: str
    source_type: str = "GENERIC"
    event_type: str
    object_key: str
    version_id: str = ""
    native_event_id: str = ""
    event_time: str = ""
    sequencer: str = ""
    etag: str = ""
    size_bytes: int | None = None
    content_type: str = ""
    raw: dict = Field(default_factory=dict)


class AdapterPayloadIn(BaseModel):
    tenant_id: str = settings.default_tenant
    catalogue_group_id: str
    payload: dict | list
    publish: bool = True


class ScheduleIn(BaseModel):
    schedule_type: str
    interval_minutes: int
    enabled: bool = True
    next_run_at: str | None = None


class PipelineIn(BaseModel):
    tenant_id: str = settings.default_tenant
    name: str
    description: str = ""
    source_group_id: str | None = None
    stages: list[str] = Field(default_factory=list)
    schedule: str = "EVENT_DRIVEN"


class IndexIn(BaseModel):
    tenant_id: str = settings.default_tenant
    name: str
    engine: str = "SOLR"
    endpoint: str | None = None
    aliases: dict[str, str] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    source_group_ids: list[str] = Field(default_factory=list)


class RoutedSearchIn(BaseModel):
    tenant_id: str = settings.default_tenant
    query: str
    index_ids: list[str] = Field(default_factory=list)
    limit: int = 20


class PiiRuleIn(BaseModel):
    tenant_id: str = settings.default_tenant
    name: str
    pattern: str
    fields: list[str] = Field(default_factory=lambda: ["content"])
    classification: str = "SENSITIVE"


class GovernancePolicyIn(BaseModel):
    tenant_id: str = settings.default_tenant
    name: str
    action: str = "RETAIN"
    selector: dict = Field(default_factory=dict)
    retention_days: int = 0
    priority: int = 100


class GovernanceRunIn(BaseModel):
    tenant_id: str = settings.default_tenant
    mode: str = "DRY_RUN"


@app.get("/api/v1/overview")
def overview(tenant_id: str = settings.default_tenant):
    groups = int(db.scalar("SELECT COUNT(*) c FROM catalogue_groups WHERE tenant_id=?", (tenant_id,), 0) or 0)
    active_groups = int(db.scalar("SELECT COUNT(*) c FROM catalogue_groups WHERE tenant_id=? AND state='ACTIVE'", (tenant_id,), 0) or 0)
    objects = int(db.scalar("SELECT COUNT(*) c FROM catalogue_objects o JOIN catalogue_groups g ON g.id=o.catalogue_group_id WHERE g.tenant_id=?", (tenant_id,), 0) or 0)
    active_objects = int(db.scalar("SELECT COUNT(*) c FROM catalogue_objects o JOIN catalogue_groups g ON g.id=o.catalogue_group_id WHERE g.tenant_id=? AND o.lifecycle_state='ACTIVE'", (tenant_id,), 0) or 0)
    bytes_total = int(db.scalar("SELECT COALESCE(SUM(o.size_bytes),0) s FROM catalogue_objects o JOIN catalogue_groups g ON g.id=o.catalogue_group_id WHERE g.tenant_id=? AND o.lifecycle_state='ACTIVE'", (tenant_id,), 0) or 0)
    indexed = int(db.scalar("SELECT COUNT(DISTINCT recon_id) c FROM search_documents WHERE tenant_id=?", (tenant_id,), 0) or 0)
    ai = int(db.scalar("SELECT COUNT(DISTINCT recon_id) c FROM ai_artifacts WHERE tenant_id=? AND status='READY'", (tenant_id,), 0) or 0)
    findings = int(db.scalar("SELECT COUNT(*) c FROM reconciliation_results r JOIN jobs j ON j.id=r.job_id WHERE j.tenant_id=?", (tenant_id,), 0) or 0)
    jobs = db.fetchall("SELECT id,job_type,status,progress,catalogue_group_id,source_group_id,target_group_id,metrics_json,created_at,completed_at FROM jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT 8", (tenant_id,))
    for j in jobs:
        j["metrics"] = db.loads(j.pop("metrics_json", "{}"), {})
    activity = db.fetchall("SELECT action,outcome,catalogue_group_id,details_json,created_at FROM audit_events WHERE tenant_id=? ORDER BY created_at DESC LIMIT 8", (tenant_id,))
    for a in activity:
        a["details"] = db.loads(a.pop("details_json", "{}"), {})
    return {
        "catalogue_groups": groups, "active_catalogue_groups": active_groups, "catalogue_objects": objects,
        "active_objects": active_objects, "bytes": bytes_total, "indexed": indexed, "ai_ready": ai,
        "index_pct": round(100 * indexed / max(active_objects, 1), 1),
        "ai_pct": round(100 * ai / max(active_objects, 1), 1), "findings": findings,
        "jobs": jobs, "activity": activity,
    }


# ----- Storage systems / catalogue registry -----
@app.get("/api/v1/storage-systems")
def storages(tenant_id: str = settings.default_tenant):
    return catalog.list_storages(tenant_id)


@app.post("/api/v1/storage-systems")
def create_storage(body: StorageIn):
    return catalog.create_storage(body.model_dump())


@app.get("/api/v1/catalogue-groups")
def catalogue_groups(tenant_id: str = settings.default_tenant):
    return catalog.list_catalogue_groups(tenant_id)


@app.post("/api/v1/catalogue-groups")
def create_catalogue_group(body: CatalogueIn):
    try:
        return catalog.create_catalogue_group(
            tenant=body.tenant_id, storage_id=body.storage_id, container_name=body.container_name,
            container_type=body.container_type, name=body.name, virtual_shards=body.virtual_shards,
            physical_shards=body.physical_shards,
        )
    except KeyError:
        raise HTTPException(404, "storage system not found")


@app.get("/api/v1/catalogue-groups/{gid}")
def catalogue_group(gid: str):
    try:
        return catalog.get_catalogue_group(gid)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")


@app.post("/api/v1/catalogue-groups/{gid}/state")
def catalogue_state(gid: str, body: CatalogueStateIn):
    try:
        return catalog.set_group_state(gid, body.state)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/catalogue-groups/{gid}/shards")
def catalogue_shards(gid: str):
    try:
        catalog.get_catalogue_group(gid)
        return catalog.list_shards(gid)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")


@app.get("/api/v1/catalogue-objects/{oid}/versions")
def catalogue_object_versions(oid: str, refresh: bool = False):
    try:
        obj = catalog.get_catalogue_object(oid)
        if refresh:
            backend = backend_from_record(catalog.backend_record_for_group(str(obj["catalogue_group_id"])))
            group = catalog.get_catalogue_group(str(obj["catalogue_group_id"]))
            for stat in backend.list_versions(catalog.payload_key_for(obj)):
                catalog.record_payload_version(str(obj["id"]), group, str(obj["object_key"]), stat.version_id,
                                               stat.etag, stat.checksum_sha256, stat.size, stat.content_type, stat.backend.raw,
                                               make_current=bool(stat.is_current))
        return catalog.list_payload_versions(oid)
    except KeyError:
        raise HTTPException(404, "catalogue object not found")


# ----- Administrative catalogue objects -----
@app.get("/api/v1/catalogue-objects")
def catalogue_objects(tenant_id: str = settings.default_tenant, catalogue_group_id: str | None = None,
                      shard_id: str | None = None, q: str = "", limit: int = 200):
    return catalog.list_catalogue_objects(tenant=tenant_id, group_id=catalogue_group_id, shard_id=shard_id, q=q, limit=limit)


@app.get("/api/v1/catalogue-objects/{oid}")
def catalogue_object(oid: str):
    try:
        return catalog.get_catalogue_object(oid)
    except KeyError:
        raise HTTPException(404, "catalogue object not found")


@app.get("/api/v1/catalogue-objects/{oid}/annotations")
def catalogue_object_annotations(oid: str):
    try:
        catalog.get_catalogue_object(oid)
        return catalog.list_annotations(oid)
    except KeyError:
        raise HTTPException(404, "catalogue object not found")


@app.get("/api/v1/catalogue-objects/{oid}/content")
def content(oid: str, request: Request):
    """Stream the authoritative payload from the catalogue group's source storage.

    PostgreSQL/psycopg returns UUID columns as ``uuid.UUID`` instances. HTTP header
    values must be strings, so always normalize catalogue/recon identifiers before
    constructing the response. Storage failures are surfaced as a controlled 502
    instead of leaking a generic application 500.
    """
    obj = None
    try:
        data, obj = processing.read_catalogue_object(oid)
        filename = str(obj.get("logical_name") or obj.get("object_key") or "payload").split("/")[-1]
        safe_filename = filename.replace('"', "'").replace("\r", "").replace("\n", "")
        return Response(
            content=data,
            media_type=obj.get("content_type") or "application/octet-stream",
            headers={
                "x-amp-recon-id": str(obj["recon_id"]),
                "x-amp-catalogue": str(obj["catalogue_group_id"]),
                "content-disposition": f'inline; filename="{safe_filename}"',
            },
        )
    except KeyError:
        raise HTTPException(404, "catalogue object not found")
    except FileNotFoundError:
        raise HTTPException(404, "payload unavailable")
    except Exception as exc:
        rid = request.headers.get("x-request-id") or "unknown"
        print(f"[payload-read] FAILED request_id={rid} object_id={oid} error={type(exc).__name__}: {exc}", flush=True)
        if obj:
            try:
                tenant = obj.get("tenant_id") or catalog.get_catalogue_group(str(obj["catalogue_group_id"]))["tenant_id"]
                catalog.audit(
                    tenant, "OBJECT_READ", group_id=str(obj["catalogue_group_id"]),
                    object_id=str(obj["id"]), recon_id=str(obj["recon_id"]),
                    outcome="FAILED", details={"error_type": type(exc).__name__, "key": obj.get("object_key")},
                    request_id=rid,
                )
            except Exception:
                pass
        raise HTTPException(502, "source storage read failed")


# ----- Connector discovery and indexing -----
@app.post("/api/v1/discovery/run")
def discovery(body: DiscoverIn):
    try:
        result = ops.discover(body.tenant_id, body.catalogue_group_id, body.prefix)
        if body.auto_index:
            result["indexing"] = processing.index_source(body.catalogue_group_id, body.prefix)
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/indexing/run")
def indexing(catalogue_group_id: str, prefix: str = ""):
    try:
        return processing.index_source(catalogue_group_id, prefix)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/reconciliation/run")
def reconcile(body: ReconcileIn):
    try:
        return ops.reconcile(
            body.tenant_id, body.catalogue_group_id, body.target, body.shard_id,
            prefix=body.prefix, storage_mode=body.storage_mode,
            verify_package_members=body.verify_package_members, limit=body.limit,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/jobs")
def jobs(tenant_id: str = settings.default_tenant, catalogue_group_id: str | None = None, limit: int = 100):
    sql = "SELECT * FROM jobs WHERE tenant_id=?"
    params: list = [tenant_id]
    if catalogue_group_id:
        sql += " AND (catalogue_group_id=? OR source_group_id=? OR target_group_id=?)"
        params += [catalogue_group_id, catalogue_group_id, catalogue_group_id]
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.fetchall(sql, params)
    for r in rows:
        r["selector"] = db.loads(r.pop("selector_json", "{}"), {})
        r["metrics"] = db.loads(r.pop("metrics_json", "{}"), {})
    return rows


@app.get("/api/v1/reconciliation/findings")
def findings(tenant_id: str = settings.default_tenant, catalogue_group_id: str | None = None,
             job_id: str | None = None, target_type: str | None = None,
             severity: str | None = None, limit: int = 200):
    sql = """SELECT r.*,o.object_key,g.name catalogue_name FROM reconciliation_results r
             JOIN jobs j ON j.id=r.job_id LEFT JOIN catalogue_objects o ON o.id=r.catalogue_object_id
             LEFT JOIN catalogue_groups g ON g.id=r.catalogue_group_id WHERE j.tenant_id=?"""
    params: list = [tenant_id]
    if catalogue_group_id:
        sql += " AND r.catalogue_group_id=?"; params.append(catalogue_group_id)
    if job_id:
        sql += " AND r.job_id=?"; params.append(job_id)
    if target_type:
        sql += " AND r.target_type=?"; params.append(target_type.upper())
    if severity:
        sql += " AND r.severity=?"; params.append(severity.upper())
    sql += " ORDER BY r.created_at DESC LIMIT ?"; params.append(limit)
    rows = db.fetchall(sql, params)
    for r in rows:
        r["details"] = db.loads(r.pop("details_json", "{}"), {})
    return rows


# ----- Continuous change capture / Kafka event backbone -----
@app.get("/api/v1/change-capture")
def change_capture(tenant_id: str = settings.default_tenant):
    return events.list_captures(tenant_id)


@app.get("/api/v1/catalogue-groups/{gid}/change-capture")
def get_change_capture(gid: str):
    try:
        catalog.get_catalogue_group(gid)
        return events.get_capture(gid)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")


@app.post("/api/v1/catalogue-groups/{gid}/change-capture")
def configure_change_capture(gid: str, body: ChangeCaptureIn):
    try:
        return events.configure_capture(gid, body.mode, body.config, body.enabled)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/catalogue-schedules")
def catalogue_schedules(tenant_id: str = settings.default_tenant, catalogue_group_id: str | None = None):
    return schedules.list(tenant_id, catalogue_group_id)


@app.post("/api/v1/catalogue-groups/{gid}/schedules")
def configure_schedule(gid: str, body: ScheduleIn):
    try:
        return schedules.configure(gid, body.schedule_type, body.interval_minutes, body.enabled, body.next_run_at)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/catalogue-schedules/{schedule_id}/run")
def run_schedule(schedule_id: str):
    try:
        return schedules.run(schedules.get(schedule_id))
    except KeyError:
        raise HTTPException(404, "schedule not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/storage-events")
def storage_events(tenant_id: str = settings.default_tenant, catalogue_group_id: str | None = None, limit: int = 200):
    return events.list_events(tenant_id, catalogue_group_id, limit)


@app.get("/api/v1/storage-events/dlq")
def storage_event_dlq(catalogue_group_id: str | None = None, limit: int = 200):
    return events.list_dlq(catalogue_group_id, limit)


@app.post("/api/v1/events/storage")
def ingest_storage_event(body: StorageEventIn, publish: bool = True):
    event = body.model_dump()
    if not event.get("event_time"):
        event["event_time"] = now()
    try:
        if publish and settings.kafka_bootstrap:
            events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
            return {"status": "QUEUED", "topic": settings.kafka_storage_topic, "event": event}
        return events.apply_event(event)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/storage-events/{event_id}/replay")
def replay_storage_event(event_id: str, publish: bool = True):
    try:
        return events.replay_event(event_id, publish=publish and bool(settings.kafka_bootstrap), bootstrap=settings.kafka_bootstrap or None)
    except KeyError:
        raise HTTPException(404, "storage event not found")
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/storage-events/dlq/{dlq_id}/replay")
def replay_dead_letter(dlq_id: str, publish: bool = True):
    try:
        return events.replay_dlq(dlq_id, publish=publish and bool(settings.kafka_bootstrap), bootstrap=settings.kafka_bootstrap or None)
    except KeyError:
        raise HTTPException(404, "dead-letter event not found")
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/event-adapters/s3-notification")
def ingest_s3_notification(body: AdapterPayloadIn, source_type: str = "MINIO_KAFKA"):
    try:
        normalized = events.normalize_s3_notification(body.payload if isinstance(body.payload, dict) else {"Records": body.payload},
                                                       group_id=body.catalogue_group_id, tenant_id=body.tenant_id, source_type=source_type)
        for event in normalized:
            if body.publish and settings.kafka_bootstrap:
                events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
            else:
                events.apply_event(event)
        return {"normalized": len(normalized), "queued": bool(body.publish and settings.kafka_bootstrap), "events": normalized}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/event-adapters/aws-eventbridge")
def ingest_eventbridge(body: AdapterPayloadIn):
    try:
        if not isinstance(body.payload, dict):
            raise ValueError("EventBridge payload must be an object")
        normalized = events.normalize_eventbridge_s3(body.payload, group_id=body.catalogue_group_id, tenant_id=body.tenant_id)
        for event in normalized:
            if body.publish and settings.kafka_bootstrap:
                events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
            else:
                events.apply_event(event)
        return {"normalized": len(normalized), "queued": bool(body.publish and settings.kafka_bootstrap), "events": normalized}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/event-adapters/hcp-mqe")
def ingest_hcp_mqe(body: AdapterPayloadIn):
    try:
        operations = body.payload if isinstance(body.payload, list) else body.payload.get("operations", [])
        normalized = events.normalize_hcp_operations(operations, group_id=body.catalogue_group_id, tenant_id=body.tenant_id)
        for event in normalized:
            if body.publish and settings.kafka_bootstrap:
                events.publish(event, settings.kafka_bootstrap, settings.kafka_storage_topic)
            else:
                events.apply_event(event)
        return {"normalized": len(normalized), "queued": bool(body.publish and settings.kafka_bootstrap), "events": normalized}
    except Exception as exc:
        raise HTTPException(400, str(exc))


# ----- Search/AI is independent of the catalogue -----
@app.get("/api/v1/search")
def search(q: str, tenant_id: str = settings.default_tenant, limit: int = 20):
    return processing.search(tenant_id, q, limit)


@app.post("/api/v1/ai/retrieve")
def retrieve(body: RetrieveIn):
    return {
        "query": body.query,
        "results": processing.search(body.tenant_id, body.query, body.top_k, body.dataset_id),
        "retrieval": "hybrid-local-beta",
        "catalogue_dependency": False,
        "note": "Production HOP writes to Solr/vector indexes directly from storage; AMP Catalogue is used later for reconciliation.",
    }


@app.get("/api/v1/datasets")
def datasets(tenant_id: str = settings.default_tenant):
    rows = db.fetchall("SELECT * FROM datasets WHERE tenant_id=? ORDER BY updated_at DESC", (tenant_id,))
    for r in rows:
        r["query"] = db.loads(r.pop("query_json", "{}"), {})
        r["members"] = int(db.scalar("SELECT COUNT(*) c FROM dataset_members WHERE dataset_id=?", (r["id"],), 0) or 0)
    return rows


@app.post("/api/v1/datasets")
def create_dataset(body: DatasetIn):
    did, ts = uid(), now()
    db.execute(
        "INSERT INTO datasets(id,tenant_id,name,description,membership_mode,query_json,version,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (did, body.tenant_id, body.name, body.description, body.membership_mode, db.dumps(body.query), 1, "ACTIVE", ts, ts),
    )
    catalog.audit(body.tenant_id, "DATASET_CREATE", details={"dataset_id": did, "name": body.name})
    return db.fetchone("SELECT * FROM datasets WHERE id=?", (did,))


@app.post("/api/v1/datasets/{did}/materialize")
def materialize(did: str):
    ds = db.fetchone("SELECT * FROM datasets WHERE id=?", (did,))
    if not ds:
        raise HTTPException(404, "dataset not found")
    q = db.loads(ds.get("query_json"), {}) or {}
    sql = "SELECT DISTINCT source_id,container_type,container_name,recon_id FROM search_documents WHERE tenant_id=?"
    params: list = [ds["tenant_id"]]
    if q.get("container_name"):
        sql += " AND container_name=?"; params.append(q["container_name"])
    if q.get("source_id"):
        sql += " AND source_id=?"; params.append(q["source_id"])
    members = db.fetchall(sql, params)
    db.execute("DELETE FROM dataset_members WHERE dataset_id=?", (did,))
    for m in members:
        db.execute(
            "INSERT INTO dataset_members(dataset_id,source_id,container_type,container_name,recon_id,added_at) VALUES(?,?,?,?,?,?)",
            (did, m["source_id"], m["container_type"], m["container_name"], m["recon_id"], now()),
        )
    return {"dataset_id": did, "members": len(members)}


# ----- Enterprise Beta: HCI-familiar pipelines, indexes, analytics and governance -----
@app.get("/api/v1/pipelines")
def pipelines(tenant_id: str = settings.default_tenant):
    return enterprise.list_pipelines(tenant_id)


@app.post("/api/v1/pipelines")
def create_pipeline(body: PipelineIn):
    return enterprise.create_pipeline(body.model_dump())


@app.post("/api/v1/pipelines/{pipeline_id}/run")
def run_pipeline(pipeline_id: str):
    try:
        run = enterprise.pipeline_run(pipeline_id)
        group_id = run.get("source_group_id")
        tenant_id = run["tenant_id"]
        if not group_id:
            return enterprise.pipeline_complete(pipeline_id, "COMPLETE", {"note": "No source scope assigned"})
        metrics = {
            "discovery": ops.discover(tenant_id, group_id),
            "indexing": processing.index_source(group_id),
            "pii": enterprise.scan_pii(tenant_id),
            "reconciliation": ops.reconcile(tenant_id, group_id, target="ALL", verify_package_members=False),
        }
        return enterprise.pipeline_complete(pipeline_id, "COMPLETE", metrics)
    except KeyError:
        raise HTTPException(404, "pipeline not found")
    except Exception as exc:
        enterprise.pipeline_complete(pipeline_id, "FAILED", {"error": str(exc)})
        raise HTTPException(400, str(exc))


@app.get("/api/v1/indexes")
def indexes(tenant_id: str = settings.default_tenant):
    return enterprise.list_indexes(tenant_id)


@app.post("/api/v1/indexes")
def create_index(body: IndexIn):
    return enterprise.create_index(body.model_dump())


@app.post("/api/v1/search/federated")
def federated_search(body: RoutedSearchIn):
    return enterprise.routed_search(body.tenant_id, body.query, processing, body.index_ids, max(1, min(body.limit, 100)))


@app.get("/api/v1/analytics/overview")
def analytics_overview(tenant_id: str = settings.default_tenant):
    return enterprise.analytics(tenant_id)


@app.get("/api/v1/pii/rules")
def pii_rules(tenant_id: str = settings.default_tenant):
    return enterprise.list_rules(tenant_id)


@app.post("/api/v1/pii/rules")
def create_pii_rule(body: PiiRuleIn):
    try:
        return enterprise.create_rule(body.model_dump())
    except (ValueError, re.error) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/pii/scan")
def run_pii_scan(tenant_id: str = settings.default_tenant):
    return enterprise.scan_pii(tenant_id)


@app.get("/api/v1/pii/findings")
def pii_findings(tenant_id: str = settings.default_tenant):
    return enterprise.pii_findings(tenant_id)


@app.get("/api/v1/governance/policies")
def governance_policies(tenant_id: str = settings.default_tenant):
    return enterprise.list_policies(tenant_id)


@app.post("/api/v1/governance/policies")
def create_governance_policy(body: GovernancePolicyIn):
    return enterprise.create_policy(body.model_dump())


@app.post("/api/v1/governance/evaluate")
def evaluate_governance(body: GovernanceRunIn):
    try:
        return enterprise.evaluate_governance(body.tenant_id, body.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/governance/actions")
def governance_actions(tenant_id: str = settings.default_tenant):
    return enterprise.governance_actions(tenant_id)


@app.get("/api/v1/monitoring")
def monitoring(tenant_id: str = settings.default_tenant):
    return enterprise.monitoring(tenant_id)



def seed_demo():
    if int(db.scalar("SELECT COUNT(*) c FROM storage_systems", (), 0) or 0) > 0:
        groups = db.fetchall("SELECT id FROM catalogue_groups WHERE tenant_id=?", (settings.default_tenant,))
        enterprise.seed_defaults(settings.default_tenant, [g["id"] for g in groups])
        return
    base = Path(settings.data_root)
    primary_root = base / "primary"
    legacy_root = base / "legacy"
    primary_root.mkdir(parents=True, exist_ok=True)
    legacy_root.mkdir(parents=True, exist_ok=True)
    samples = {
        "contracts/uk-msa.txt": "UK master services agreement. Customer may terminate for convenience with ninety days notice.",
        "records/aus-policy.json": '{"recordType":"CUSTOMER_RECORD","jurisdiction":"AUS","retentionYears":7}',
    }
    for key, text in samples.items():
        p = legacy_root / key; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    pstore = catalog.create_storage({"tenant_id": settings.default_tenant, "name": "Demo Primary", "kind": "LOCAL", "role": "PRIMARY", "root_path": str(primary_root)})
    lstore = catalog.create_storage({"tenant_id": settings.default_tenant, "name": "Demo Legacy", "kind": "LOCAL", "role": "SECONDARY", "root_path": str(legacy_root)})
    pg = catalog.create_catalogue_group(tenant=settings.default_tenant, storage_id=pstore["id"], container_name=".", container_type="DIRECTORY", name="Demo Primary", physical_shards=2)
    lg = catalog.create_catalogue_group(tenant=settings.default_tenant, storage_id=lstore["id"], container_name=".", container_type="DIRECTORY", name="Demo Legacy", physical_shards=4)
    ops.discover(settings.default_tenant, lg["id"])
    processing.index_source(lg["id"])
    enterprise.seed_defaults(settings.default_tenant, [pg["id"], lg["id"]])
