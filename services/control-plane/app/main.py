from __future__ import annotations
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .db import Database
from .services.catalog import CatalogService, now, uid
from .services.storage import backend_from_record, BackendOperationError
from .services.processing import ProcessingService
from .services.operations import OperationsService
from .services.events import EventService
from .services.scheduler import ScheduleService
from .services.hcp_gateway import HCPGatewayService
from .services.s3_gateway import S3GatewayService, S3AuthError
from .services.gateway_response import filter_headers, normalized_error, normalized_s3_error_xml, raw_error_body

app = FastAPI(title="AMP Enterprise Beta API", version=settings.version, docs_url="/docs", redoc_url="/redoc")
db = Database(settings.database_url)
catalog = CatalogService(db)
processing = ProcessingService(db, catalog)
ops = OperationsService(db, catalog)
events = EventService(db, catalog)
schedules = ScheduleService(db, catalog, ops)
hcp_gateway = HCPGatewayService(db, catalog)
s3_gateway = S3GatewayService(db, catalog)
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
    package_placement_mode: str = "AMP_MANAGED_HASH"
    package_root_prefix: str = ".amp/objects"
    package_hash_levels: int = 2
    package_hash_segment_chars: int = 2


class PackageLayoutIn(BaseModel):
    mode: str = "AMP_MANAGED_HASH"
    root_prefix: str = ".amp/objects"
    hash_levels: int = 2
    hash_segment_chars: int = 2


class CatalogueStateIn(BaseModel):
    state: str


class DiscoverIn(BaseModel):
    tenant_id: str = settings.default_tenant
    catalogue_group_id: str
    prefix: str = ""
    auto_index: bool = False


class MigrationIn(BaseModel):
    tenant_id: str = settings.default_tenant
    source_group_id: str
    target_group_id: str
    prefix: str = ""
    dry_run: bool = False


class ReconcileIn(BaseModel):
    tenant_id: str = settings.default_tenant
    catalogue_group_id: str
    target: str = "ALL"
    shard_id: str | None = None


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


class GatewayRouteIn(BaseModel):
    tenant_id: str = settings.default_tenant
    namespace: str
    catalogue_group_id: str
    object_prefix: str = ""
    response_mode: str = "AMP_NORMALIZED"
    backend_header_policy: str = "SELECTED"
    add_amp_request_id: bool = True
    capture_backend_response: bool = True
    max_captured_error_body_bytes: int = 65536


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
            physical_shards=body.physical_shards, package_placement_mode=body.package_placement_mode,
            package_root_prefix=body.package_root_prefix, package_hash_levels=body.package_hash_levels,
            package_hash_segment_chars=body.package_hash_segment_chars,
        )
    except KeyError:
        raise HTTPException(404, "storage system not found")


@app.get("/api/v1/catalogue-groups/{gid}")
def catalogue_group(gid: str):
    try:
        return catalog.get_catalogue_group(gid)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")


@app.get("/api/v1/catalogue-groups/{gid}/package-layout")
def catalogue_package_layout(gid: str):
    try:
        g = catalog.get_catalogue_group(gid)
        return {
            "catalogue_group_id": str(g["id"]),
            "mode": g.get("package_placement_mode") or "AMP_MANAGED_HASH",
            "root_prefix": g.get("package_root_prefix") or ".amp/objects",
            "hash_levels": int(g.get("package_hash_levels") or 2),
            "hash_segment_chars": int(g.get("package_hash_segment_chars") or 2),
            "applies_to": "FUTURE_MANAGED_WRITES",
        }
    except KeyError:
        raise HTTPException(404, "catalogue group not found")


@app.post("/api/v1/catalogue-groups/{gid}/package-layout")
def configure_catalogue_package_layout(gid: str, body: PackageLayoutIn):
    try:
        return catalog.set_package_layout(
            gid, mode=body.mode, root_prefix=body.root_prefix,
            hash_levels=body.hash_levels, hash_segment_chars=body.hash_segment_chars,
        )
    except KeyError:
        raise HTTPException(404, "catalogue group not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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


# ----- Gateway namespace routing / HCP REST compatibility -----
@app.get("/api/v1/gateway-routes")
def gateway_routes(tenant_id: str = settings.default_tenant):
    return catalog.list_gateway_routes(tenant_id)


@app.post("/api/v1/gateway-routes")
def configure_gateway_route(body: GatewayRouteIn):
    try:
        return catalog.configure_gateway_route(
            tenant=body.tenant_id, namespace=body.namespace,
            catalogue_group_id=body.catalogue_group_id, object_prefix=body.object_prefix,
            response_mode=body.response_mode, backend_header_policy=body.backend_header_policy,
            add_amp_request_id=body.add_amp_request_id, capture_backend_response=body.capture_backend_response,
            max_captured_error_body_bytes=body.max_captured_error_body_bytes,
        )
    except KeyError:
        raise HTTPException(404, "catalogue group not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/v1/backend-transactions")
def backend_transactions(tenant_id: str = settings.default_tenant, limit: int = 100):
    return catalog.list_backend_transactions(tenant_id, limit)


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


@app.post("/api/v1/catalogue-objects/upload")
async def upload(catalogue_group_id: str = Form(...), object_key: str = Form(...), file: UploadFile = File(...)):
    try:
        group = catalog.get_catalogue_group(catalogue_group_id)
    except KeyError:
        raise HTTPException(404, "catalogue group not found")
    data = await file.read()
    backend = backend_from_record(catalog.backend_record_for_group(catalogue_group_id))
    stat = backend.put(object_key, data, file.content_type or "application/octet-stream")
    obj = catalog.upsert_catalogue_object(
        group_id=catalogue_group_id, object_key=object_key, version_id=stat.version_id or "",
        size_bytes=stat.size, etag=stat.etag, checksum=stat.checksum_sha256,
        content_type=file.content_type or stat.content_type, source_mode="GATEWAY",
    )
    catalog.audit(group["tenant_id"], "GATEWAY_WRITE", group_id=catalogue_group_id,
                  object_id=obj["id"], recon_id=obj["recon_id"], details={"key": object_key})
    return obj


@app.get("/api/v1/catalogue-objects/{oid}/content")
def content(oid: str, request: Request, hydrate_target_group_id: str | None = None):
    """Stream the authoritative payload from the catalogue group's source storage.

    PostgreSQL/psycopg returns UUID columns as ``uuid.UUID`` instances. HTTP header
    values must be strings, so always normalize catalogue/recon identifiers before
    constructing the response. Storage failures are surfaced as a controlled 502
    instead of leaking a generic application 500.
    """
    obj = None
    try:
        data, obj = processing.read_catalogue_object(oid, hydrate_target_group_id)
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


def _gateway_policy(tenant: str, namespace: str) -> dict:
    try:
        return catalog.get_gateway_route(tenant, namespace)
    except Exception:
        return {"id": None, "catalogue_group_id": None, "response_mode": "AMP_NORMALIZED", "backend_header_policy": "SELECTED",
                "add_amp_request_id": True, "capture_backend_response": True, "max_captured_error_body_bytes": 65536}


def _backend_info(obj: dict | None) -> dict:
    return (obj or {}).get("_backend") or {}


def _capture_backend(request: Request, tenant: str, namespace: str, protocol: str, operation: str,
                     logical_key: str, obj: dict | None = None, err: BackendOperationError | None = None):
    policy = _gateway_policy(tenant, namespace)
    if not bool(policy.get("capture_backend_response", True)):
        return
    b = _backend_info(obj)
    raw = err.raw if err else b.get("raw") or {}
    headers = err.headers if err else b.get("headers") or {}
    status = err.status if err else b.get("status")
    code = err.code if err else b.get("code") or ""
    max_body = int(policy.get("max_captured_error_body_bytes") or 65536)
    body = (err.body if err else "")[:max_body]
    group_id = str(policy.get("catalogue_group_id") or (obj or {}).get("catalogue_group_id") or "") or None
    backend_kind = str((obj or {}).get("storage_kind") or "")
    if not backend_kind and group_id:
        try: backend_kind = str(catalog.backend_record_for_group(group_id).get("kind") or "")
        except Exception: pass
    catalog.record_backend_transaction(tenant_id=tenant, route_id=str(policy.get("id") or "") or None,
        group_id=group_id, object_id=str((obj or {}).get("id") or "") or None,
        request_id=str(getattr(request.state,"request_id","")), protocol=protocol, operation=operation,
        logical_key=logical_key, backend_kind=backend_kind, backend_status=status, backend_code=code,
        outcome="FAILED" if err else "SUCCESS", headers=headers, raw_response=raw, error_body=body)


def _backend_success_headers(policy: dict, obj: dict | None, extra: dict | None = None) -> dict[str,str]:
    b = _backend_info(obj); headers = filter_headers(b.get("headers") or {}, str(policy.get("backend_header_policy") or "SELECTED"))
    if extra: headers.update({str(k):str(v) for k,v in extra.items() if v is not None and str(v)!=""})
    return headers


def _backend_error_response(request: Request, tenant: str, namespace: str, protocol: str, resource: str,
                            operation: str, exc: BackendOperationError):
    policy = _gateway_policy(tenant, namespace); rid=str(getattr(request.state,"request_id",""))
    _capture_backend(request, tenant, namespace, protocol, operation, resource, err=exc)
    mode=str(policy.get("response_mode") or "AMP_NORMALIZED").upper()
    headers=filter_headers(exc.headers, str(policy.get("backend_header_policy") or "SELECTED"))
    if bool(policy.get("add_amp_request_id", True)): headers["x-amp-request-id"]=rid
    if mode=="RAW_BACKEND":
        body,media=raw_error_body(exc, protocol, resource)
        return Response(content=body,status_code=exc.status,media_type=media,headers=headers)
    include=mode=="AMP_NORMALIZED_WITH_BACKEND"
    if protocol.upper()=="S3":
        return Response(content=normalized_s3_error_xml(exc.status,rid,backend=exc,include_backend=include,resource=resource),
                        status_code=exc.status,media_type="application/xml",headers=headers)
    import json as _json
    return Response(content=_json.dumps(normalized_error(exc.status,rid,backend=exc,include_backend=include)),
                    status_code=exc.status,media_type="application/json",headers=headers)


# ----- HCP REST compatibility subset -----
@app.api_route("/rest/{tenant}/{namespace}/{object_path:path}", methods=["PUT", "GET", "HEAD", "DELETE"])
async def hcp_rest(tenant: str, namespace: str, object_path: str, request: Request):
    qtype = (request.query_params.get("type") or "").lower()
    annotation_name = request.query_params.get("annotation") or ""
    version_id = request.query_params.get("versionId") or request.query_params.get("version") or ""
    is_annotation = qtype == "custom-metadata"
    policy = _gateway_policy(tenant, namespace)

    def success(obj: dict | None, *, normalized_status: int, content: bytes | None = None,
                media_type: str | None = None, extra: dict | None = None) -> Response:
        mode = str(policy.get("response_mode") or "AMP_NORMALIZED").upper()
        b = _backend_info(obj)
        status = int(b.get("status") or normalized_status) if mode == "RAW_BACKEND" else normalized_status
        if mode == "RAW_BACKEND":
            headers = filter_headers(b.get("headers") or {}, str(policy.get("backend_header_policy") or "SELECTED"))
        else:
            headers = _backend_success_headers(policy, obj, extra)
            if obj:
                headers.setdefault("x-amp-object-id", str(obj.get("id") or obj.get("object_id") or ""))
                headers.setdefault("x-amp-recon-id", str(obj.get("recon_id") or ""))
                if obj.get("catalogue_group_id"): headers.setdefault("x-amp-catalogue", str(obj.get("catalogue_group_id")))
                native = b.get("native_version_id") or obj.get("version_id")
                if native: headers.setdefault("x-amp-version-id", str(native))
                checksum = b.get("checksum_sha256") or obj.get("checksum_sha256")
                if checksum: headers.setdefault("x-amp-checksum-sha256", str(checksum))
            if mode == "AMP_NORMALIZED_WITH_BACKEND":
                headers["x-amp-backend-status"] = str(b.get("status") or "")
                if b.get("request_id"): headers["x-amp-backend-request-id"] = str(b.get("request_id"))
        if bool(policy.get("add_amp_request_id", True)):
            headers["x-amp-request-id"] = str(getattr(request.state,"request_id",""))
        return Response(content=content, status_code=status, media_type=media_type, headers=headers)

    try:
        if is_annotation:
            if not annotation_name:
                raise HTTPException(400, "annotation query parameter is required for custom-metadata")
            if request.method == "PUT":
                data = await request.body()
                ann = hcp_gateway.put_annotation(tenant, namespace, object_path, annotation_name, data,
                    request.headers.get("content-type") or "application/octet-stream")
                _capture_backend(request, tenant, namespace, "HCP_REST", "PUT_ANNOTATION", object_path, ann)
                return success(ann, normalized_status=201, extra={"x-amp-sidecar-key":ann.get("sidecar_key"),
                    "x-hcp-annotation":ann.get("annotation_name"),"x-amp-annotation-version":ann.get("annotation_version")})
            if request.method == "GET":
                data,obj,ann = hcp_gateway.get_annotation(tenant, namespace, object_path, annotation_name, version_id)
                ann_for_resp={**obj,**ann,"_backend":ann.get("_backend") or {}}
                _capture_backend(request, tenant, namespace, "HCP_REST", "GET_ANNOTATION", object_path, ann_for_resp)
                return success(ann_for_resp, normalized_status=200, content=data, media_type=ann["content_type"],
                    extra={"x-amp-sidecar-key":ann.get("sidecar_key"),"x-hcp-annotation":ann.get("annotation_name"),
                           "x-amp-annotation-version":ann.get("annotation_version")})
            if request.method == "HEAD":
                obj,ann=hcp_gateway.head_annotation(tenant, namespace, object_path, annotation_name, version_id)
                return success(obj,normalized_status=200,extra={"content-type":ann.get("content_type"),
                    "content-length":ann.get("size_bytes"),"x-amp-sidecar-key":ann.get("sidecar_key"),
                    "x-hcp-annotation":ann.get("annotation_name")})
            hcp_gateway.delete_annotation(tenant, namespace, object_path, annotation_name)
            return success(None,normalized_status=204)

        if request.method == "PUT":
            data=await request.body()
            obj=hcp_gateway.put_object(tenant, namespace, object_path, data,
                request.headers.get("content-type") or "application/octet-stream")
            _capture_backend(request,tenant,namespace,"HCP_REST","PUT",object_path,obj)
            return success(obj,normalized_status=201,extra={"etag":obj.get("etag")})
        if request.method == "GET":
            data,obj=hcp_gateway.get_object(tenant,namespace,object_path,version_id)
            _capture_backend(request,tenant,namespace,"HCP_REST","GET",object_path,obj)
            return success(obj,normalized_status=200,content=data,media_type=obj.get("content_type") or "application/octet-stream",
                           extra={"etag":obj.get("etag")})
        if request.method == "HEAD":
            obj,stat=hcp_gateway.head_object(tenant,namespace,object_path,version_id)
            _capture_backend(request,tenant,namespace,"HCP_REST","HEAD",object_path,obj)
            return success(obj,normalized_status=200,extra={"content-type":stat.content_type,"content-length":stat.size,"etag":stat.etag})
        obj=hcp_gateway.delete_object(tenant,namespace,object_path)
        _capture_backend(request,tenant,namespace,"HCP_REST","DELETE",object_path,obj)
        return success(obj,normalized_status=204)
    except BackendOperationError as exc:
        return _backend_error_response(request,tenant,namespace,"HCP_REST",object_path,request.method,exc)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, "HCP namespace route not found")
    except FileNotFoundError:
        raise HTTPException(404, "object or annotation not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        print(f"[hcp-rest] FAILED {request.method} {tenant}/{namespace}/{object_path}: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(502, "backend storage operation failed")

# ----- Discovery / HOP indexing / migrations -----
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


@app.post("/api/v1/migrations")
def migration(body: MigrationIn):
    try:
        return ops.migrate(body.tenant_id, body.source_group_id, body.target_group_id, body.prefix, body.dry_run)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/reconciliation/run")
def reconcile(body: ReconcileIn):
    try:
        return ops.reconcile(body.tenant_id, body.catalogue_group_id, body.target, body.shard_id)
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
def findings(tenant_id: str = settings.default_tenant, catalogue_group_id: str | None = None, limit: int = 200):
    sql = """SELECT r.*,o.object_key,g.name catalogue_name FROM reconciliation_results r
             JOIN jobs j ON j.id=r.job_id LEFT JOIN catalogue_objects o ON o.id=r.catalogue_object_id
             LEFT JOIN catalogue_groups g ON g.id=r.catalogue_group_id WHERE j.tenant_id=?"""
    params: list = [tenant_id]
    if catalogue_group_id:
        sql += " AND r.catalogue_group_id=?"; params.append(catalogue_group_id)
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



# ----- S3-compatible client front door (path-style beta subset) -----
def _s3_xml_error(code: str, message: str, resource: str, status: int = 400):
    return Response(content=s3_gateway.error_xml(code, message, resource), status_code=status,
                    media_type="application/xml")


@app.api_route("/{bucket}", methods=["GET", "HEAD"])
async def s3_bucket(bucket: str, request: Request):
    # Registered after all AMP-specific routes so /api, /rest, /docs, etc. keep precedence.
    try:
        tenant = s3_gateway.verify_sigv4(request, b"")
        s3_gateway._route(tenant, bucket)  # validates bucket/namespace route
        if request.method == "HEAD":
            return Response(status_code=200)
        prefix = request.query_params.get("prefix") or ""
        max_keys = int(request.query_params.get("max-keys") or "1000")
        token = request.query_params.get("continuation-token")
        delimiter = request.query_params.get("delimiter") or ""
        body = s3_gateway.list_v2(tenant, bucket, prefix=prefix, max_keys=max_keys,
                                  continuation_token=token, delimiter=delimiter)
        return Response(content=body, media_type="application/xml")
    except S3AuthError as exc:
        code = str(exc)
        status = 403 if code not in {"InvalidAccessKeyId"} else 403
        return _s3_xml_error(code, code, f"/{bucket}", status)
    except KeyError:
        return _s3_xml_error("NoSuchBucket", "The specified bucket does not exist", f"/{bucket}", 404)
    except ValueError as exc:
        return _s3_xml_error("InvalidArgument", str(exc), f"/{bucket}", 400)


@app.api_route("/{bucket}/{object_path:path}", methods=["PUT", "GET", "HEAD", "DELETE"])
async def s3_object(bucket: str, object_path: str, request: Request):
    resource=f"/{bucket}/{object_path}"
    tenant=settings.default_tenant
    try:
        body=await request.body() if request.method=="PUT" else b""
        tenant=s3_gateway.verify_sigv4(request,body)
        policy=_gateway_policy(tenant,bucket)
        version_id=request.query_params.get("versionId") or ""

        def success(obj: dict | None, *, normalized_status: int, content: bytes | None=None,
                    media_type: str | None=None, extra: dict | None=None) -> Response:
            mode=str(policy.get("response_mode") or "AMP_NORMALIZED").upper(); b=_backend_info(obj)
            status=int(b.get("status") or normalized_status) if mode=="RAW_BACKEND" else normalized_status
            if mode=="RAW_BACKEND":
                headers=filter_headers(b.get("headers") or {},str(policy.get("backend_header_policy") or "SELECTED"))
            else:
                headers=_backend_success_headers(policy,obj,extra)
                if obj:
                    etag=obj.get("etag") or b.get("etag")
                    if etag: headers.setdefault("etag",f'"{str(etag).strip(chr(34))}"')
                    native=b.get("native_version_id") or obj.get("version_id")
                    if native: headers.setdefault("x-amz-version-id",str(native))
                    checksum=b.get("checksum_sha256") or obj.get("checksum_sha256")
                    if checksum: headers.setdefault("x-amz-checksum-sha256",str(checksum))
                    headers.setdefault("x-amp-object-id",str(obj.get("id") or "")); headers.setdefault("x-amp-recon-id",str(obj.get("recon_id") or ""))
                if mode=="AMP_NORMALIZED_WITH_BACKEND":
                    headers["x-amp-backend-status"]=str(b.get("status") or "")
                    if b.get("request_id"): headers["x-amp-backend-request-id"]=str(b.get("request_id"))
            if bool(policy.get("add_amp_request_id",True)): headers["x-amp-request-id"]=str(getattr(request.state,"request_id",""))
            return Response(content=content,status_code=status,media_type=media_type,headers=headers)

        if "tagging" in request.query_params and request.method=="GET":
            _,obj=s3_gateway.get(tenant,bucket,object_path,version_id)
            tags=s3_gateway.user_tags(obj)
            tag_xml="".join(f"<Tag><Key>{__import__('html').escape(k)}</Key><Value>{__import__('html').escape(v)}</Value></Tag>" for k,v in sorted(tags.items()))
            return Response(content=('<?xml version="1.0" encoding="UTF-8"?>' + '<Tagging xmlns="http://s3.amazonaws.com/doc/2006-03-01/">' + f'<TagSet>{tag_xml}</TagSet></Tagging>'),media_type="application/xml")

        if request.method=="PUT":
            metadata={k[11:].lower():v for k,v in request.headers.items() if k.lower().startswith("x-amz-meta-")}
            from urllib.parse import parse_qsl
            tags=dict(parse_qsl(request.headers.get("x-amz-tagging",""),keep_blank_values=True))
            obj=s3_gateway.put(tenant,bucket,object_path,body,request.headers.get("content-type") or "application/octet-stream",metadata,tags)
            _capture_backend(request,tenant,bucket,"S3","PUT",object_path,obj)
            return success(obj,normalized_status=200)

        if request.method=="GET":
            data,obj=s3_gateway.get(tenant,bucket,object_path,version_id)
            _capture_backend(request,tenant,bucket,"S3","GET",object_path,obj)
            metadata=s3_gateway.user_metadata(obj); headers={f"x-amz-meta-{k}":v for k,v in metadata.items()}; headers["accept-ranges"]="bytes"
            range_header=request.headers.get("range"); status=200; content=data
            if range_header and range_header.startswith("bytes="):
                spec=range_header[6:].split(",",1)[0]; start_s,end_s=spec.split("-",1)
                if start_s=="": length=int(end_s); start,end=max(0,len(data)-length),len(data)-1
                else: start=int(start_s); end=int(end_s) if end_s else len(data)-1
                if start>=len(data) or start<0 or end<start:
                    return _s3_xml_error("InvalidRange","The requested range is not satisfiable",resource,416)
                end=min(end,len(data)-1); content=data[start:end+1]; headers["content-range"]=f"bytes {start}-{end}/{len(data)}"; status=206
            return success(obj,normalized_status=status,content=content,media_type=obj.get("content_type") or "application/octet-stream",extra=headers)

        if request.method=="HEAD":
            obj,stat=s3_gateway.head(tenant,bucket,object_path,version_id)
            _capture_backend(request,tenant,bucket,"S3","HEAD",object_path,obj)
            metadata=s3_gateway.user_metadata(obj); headers={"content-type":stat.content_type,"content-length":stat.size,"accept-ranges":"bytes"}
            headers.update({f"x-amz-meta-{k}":v for k,v in metadata.items()})
            return success(obj,normalized_status=200,extra=headers)

        obj=s3_gateway.delete(tenant,bucket,object_path)
        if obj: _capture_backend(request,tenant,bucket,"S3","DELETE",object_path,obj)
        return success(obj,normalized_status=204)

    except BackendOperationError as exc:
        return _backend_error_response(request,tenant,bucket,"S3",resource,request.method,exc)
    except S3AuthError as exc:
        code=str(exc); return _s3_xml_error(code,code,resource,403)
    except KeyError:
        return _s3_xml_error("NoSuchBucket","The specified bucket does not exist",resource,404)
    except FileNotFoundError:
        return _s3_xml_error("NoSuchKey","The specified key does not exist",resource,404)
    except ValueError as exc:
        return _s3_xml_error("InvalidArgument",str(exc),resource,400)
    except Exception as exc:
        print(f"[s3] FAILED {request.method} {resource}: {type(exc).__name__}: {exc}",flush=True)
        return _s3_xml_error("InternalError","We encountered an internal error",resource,500)

def seed_demo():
    if int(db.scalar("SELECT COUNT(*) c FROM storage_systems", (), 0) or 0) > 0:
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
