from __future__ import annotations
import json
import os
import tempfile
import threading
from functools import wraps
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .archive import ArchiveService, Conflict
from .security import current_identity, Identity
from .workbench import WorkbenchService


def install(app, db, catalog, processing, enterprise, settings):
    archive = ArchiveService(db, catalog, processing, settings.data_root)
    workbench = WorkbenchService(db, catalog, processing, settings.data_root)
    archive.workbench, workbench.archive = workbench, archive
    from .hcp_demo import HCPDemoService, FEATURES
    hcp = HCPDemoService(db, catalog, processing, workbench, archive, enterprise)
    hcp.init_schema()
    app.state.hcp = hcp
    processing.workbench = workbench
    app.state.archive, app.state.workbench = archive, workbench
    router = APIRouter(prefix="/api/v1/beta")
    stop = threading.Event()

    def identity():
        value = current_identity.get()
        if value is None:
            raise HTTPException(401, "identity required")
        return value

    def admin():
        who = identity()
        if not who.admin:
            raise HTTPException(403, "operator role required")
        return who

    def translate(fn):
        @wraps(fn)
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except HTTPException:
                raise
            except KeyError:
                raise HTTPException(404, "resource not found")
            except PermissionError as exc:
                raise HTTPException(403, str(exc))
            except Conflict as exc:
                raise HTTPException(409, str(exc))
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc))
        return call

    @router.get("/hcp/workflows")
    @translate
    def hcp_workflows():
        return hcp.workflows(admin().tenant)

    @router.post("/hcp/workflows")
    @translate
    def hcp_create(body: dict):
        return hcp.create(admin().tenant, body)

    @router.post("/hcp/demo/setup")
    @translate
    def hcp_setup():
        return hcp.setup_demo(admin().tenant)

    @router.get("/hcp/features")
    def hcp_features():
        return [{"issue":i,"name":n,"mode":m,"action":a} for i,n,m,a in FEATURES]

    @router.post("/hcp/workflows/{wid}/preview")
    @translate
    def hcp_preview(wid: str, body: dict):
        return hcp.preview(admin().tenant,wid,body["key"])

    @router.post("/hcp/workflows/{wid}/run", status_code=202)
    @translate
    def hcp_enqueue(wid: str):
        return hcp.enqueue(admin().tenant,wid)

    @router.get("/hcp/runs")
    def hcp_runs():
        who=admin()
        return [hcp.run(who.tenant,row["id"]) for row in db.fetchall("SELECT id FROM hcp_runs WHERE tenant_id=? ORDER BY created_at DESC LIMIT 20",(who.tenant,))]

    @router.post("/hcp/runs/{rid}/execute")
    @translate
    def hcp_execute(rid: str):
        return hcp.execute(admin().tenant,rid)

    @router.post("/hcp/workflows/{wid}/feature/{action}")
    @translate
    def hcp_feature(wid: str, action: str):
        return hcp.feature(admin(),wid,action)

    @router.post("/hcp/workflows/{wid}/solr")
    @translate
    def hcp_solr(wid: str, body: dict):
        from .solr import SolrPair
        who=admin();workflow=hcp.workflow(who.tenant,wid)
        pair=SolrPair(body);result=pair.provision(int(body.get("shards",1)))
        workflow["config"]["solr_pair"]=body
        db.execute("UPDATE hcp_workflows SET config_json=? WHERE id=?",(db.dumps(workflow["config"]),wid))
        return result

    @router.post("/hcp/workflows/{wid}/search")
    @translate
    def hcp_search(wid: str, body: dict):
        from .solr import SolrPair
        who=identity();workflow=hcp.workflow(who.tenant,wid)
        authorized=workbench.search(who,"",[{"field":"source_group_id","value":workflow["group_id"]}],1000)["results"]
        pair=workflow["config"].get("solr_pair")
        if pair:
            result=SolrPair(pair).search(who.tenant,body.get("query",""),authorized,body.get("filters"))
            catalog.audit(who.tenant,"HCP_SOLR_SEARCH",actor=who.actor,details={"hits":result["total"]})
            return result
        return workbench.search(who,body.get("query",""),[{"field":"source_group_id","value":workflow["group_id"]}]+body.get("filters",[]),int(body.get("limit",100)))

    @router.get("/identity")
    def whoami():
        who = identity()
        return {"tenant": who.tenant, "actor": who.actor, "roles": who.roles, "groups": who.groups}

    @router.get("/capabilities")
    def capabilities():
        return {"version": settings.version, "search_engine": "LOCAL_BETA", "gateway": "RETIRED",
            "archive": {"local_integrity": "TESTED", "s3_writes": "REQUIRES_ENDPOINT_VALIDATION",
                "azure_writes": "REQUIRES_ENDPOINT_VALIDATION", "native_hcp_rest": "PLANNED",
                "sftp": "REQUIRES_ENDPOINT_VALIDATION", "mount": "LOCAL_CONTRACT_TESTED"},
            "hop": "EXTERNAL_RUNNER_REQUIRES_QUALIFICATION", "native_governance": "PLAN_ONLY",
            "ai": "DETERMINISTIC_ASSISTANTS", "export": "ASYNC_LOCAL_BOUNDED",
            "schema_and_rollover": "REGISTRY_AND_PLAN_ONLY", "hcp_native_rest": "CONTRACT_TESTED_REQUIRES_VM", "hcp_demo": "SIMULATOR_AVAILABLE", "solr_split": "OPTIONAL_NATIVE_PAIR", "identity": "SERVER_CONFIGURED_TOKENS"}

    @router.get("/archive/profiles")
    @translate
    def profiles():
        return archive.profiles(admin().tenant)

    @router.post("/archive/profiles")
    @translate
    def create_profile(body: dict):
        return archive.create_profile(admin().tenant, body["name"], body["config"])

    @router.get("/archive/items")
    @translate
    def items():
        who = identity()
        values = archive.items(who.tenant)
        return values if who.admin else [item for item in values if workbench.allowed(who, workbench.archive_access_fields(item))]

    @router.get("/archive/items/{iid}")
    @translate
    def item(iid: str):
        who = identity()
        value = archive.item(who.tenant, iid)
        if not workbench.allowed(who, workbench.archive_access_fields(value)):
            raise PermissionError("record access denied")
        return value

    @router.post("/archive/upload", status_code=202)
    async def upload(profile_id: str = Form(...), business_id: str = Form(...), revision: str = Form(...),
            idempotency_key: str = Form(...), metadata: str = Form("{}"), batch_id: str = Form(""), file: UploadFile = File(...)):
        who = admin()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=archive.root, delete=False) as out:
                temporary = out.name
                total = 0
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > int(os.getenv("AMP_MAX_UPLOAD_BYTES", str(1024 ** 3))):
                        raise HTTPException(413, "configured upload limit exceeded")
                    out.write(chunk)
            return archive.submit(who.tenant, profile_id, temporary, file.filename or "document", business_id,
                revision, json.loads(metadata), idempotency_key, batch_id)
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        except KeyError:
            raise HTTPException(404, "profile not found")
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            await file.close()

    @router.post("/archive/package", status_code=202)
    async def package(profile_id: str = Form(...), manifest_name: str = Form("manifest.json"), batch_id: str = Form("package"), file: UploadFile = File(...)):
        from .packages import ingest_package
        who = admin()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=archive.root, delete=False) as out:
                temporary = out.name
                total = 0
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > int(os.getenv("AMP_MAX_UPLOAD_BYTES", str(1024 ** 3))):
                        raise HTTPException(413, "configured upload limit exceeded")
                    out.write(chunk)
            return ingest_package(archive, who.tenant, profile_id, temporary, manifest_name, batch_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc))
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            await file.close()

    @router.post("/archive/items/{iid}/process")
    @translate
    def process(iid: str):
        return archive.process(admin().tenant, iid)

    @router.post("/archive/profiles/{pid}/collect")
    @translate
    def collect(pid: str, body: dict):
        return archive.collect(admin().tenant, pid, body)

    @router.post("/archive/reconcile")
    @translate
    def reconcile():
        return archive.reconcile(admin().tenant)

    @router.get("/archive/items/{iid}/download")
    @translate
    def download(iid: str):
        who = identity()
        value = item(iid)
        fd, target = tempfile.mkstemp(dir=archive.root)
        os.close(fd)
        try:
            archive.download(who.tenant, iid, target)
        except Exception:
            Path(target).unlink(missing_ok=True)
            raise
        return FileResponse(target, filename=Path(value["name"]).name,
            background=BackgroundTask(Path(target).unlink, missing_ok=True))

    @router.post("/search")
    @translate
    def search(body: dict):
        return workbench.search(identity(), body.get("query", ""), body.get("filters"), int(body.get("limit", 100)), body.get("index_ids"))

    @router.post("/query-plan")
    @translate
    def query_plan(body: dict):
        return workbench.query_plan(identity(), body["text"])

    @router.post("/index-plan")
    @translate
    def index_plan(body: dict):
        admin()
        return workbench.index_plan(body)

    @router.post("/schemas")
    @translate
    def schema(body: dict):
        return workbench.schema(admin(), body["name"], body["fields"])

    @router.get("/analytics")
    @translate
    def analytics(field: str = "content_type"):
        return workbench.aggregate(identity(), field)

    @router.get("/dashboards/recommendations")
    @translate
    def recommended():
        return workbench.recommend_dashboards(identity())

    @router.post("/cost-estimate")
    @translate
    def cost(body: dict):
        return workbench.cost(identity(), float(body["rate_per_gib_month"]), int(body.get("months", 12)), float(body.get("monthly_growth_percent", 0)))

    @router.post("/access-maps")
    @translate
    def access_map(body: dict):
        return workbench.add_access(admin().tenant, body["principal"], body["effect"], body["selector"])

    @router.get("/access-maps")
    def access_maps():
        return db.fetchall("SELECT * FROM access_maps WHERE tenant_id=?", (admin().tenant,))

    @router.post("/classifiers")
    @translate
    def classifier(body: dict):
        from ..services.catalog import now, uid
        who = admin()
        rid = uid()
        db.execute("INSERT INTO beta_classifiers VALUES(?,?,?,?,?,?)", (rid, who.tenant, body["name"], db.dumps(body["selector"]), db.dumps(body["tags"]), now()))
        return {"id": rid, "mode": "RULE_BASED"}

    @router.post("/jobs", status_code=202)
    @translate
    def job_create(body: dict):
        who = identity()
        if body["kind"] in {"PII", "CLASSIFY", "BULK_PLAN", "HOP_RUN"}:
            admin()
        return workbench.new_job(who, body["kind"], body.get("request", {}))

    @router.get("/jobs")
    def jobs():
        who = identity()
        rows = db.fetchall("SELECT id FROM beta_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT 100", (who.tenant,))
        result = []
        for row in rows:
            try:
                result.append(workbench.job(who, row["id"]))
            except KeyError:
                pass
        return result

    @router.get("/jobs/{jid}")
    @translate
    def job(jid: str):
        return workbench.job(identity(), jid)

    @router.post("/jobs/{jid}/run")
    @translate
    def job_run(jid: str):
        return workbench.run_job(identity(), jid)

    @router.post("/jobs/{jid}/download-token")
    @translate
    def token(jid: str):
        return workbench.download_token(identity(), jid)

    @router.get("/jobs/{jid}/download")
    @translate
    def job_download(jid: str, token: str):
        return FileResponse(workbench.download_path(identity(), jid, token), filename="amp-export-" + jid + ".zip")

    @router.post("/schemas/reproject")
    @translate
    def reproject(body: dict):
        return workbench.reproject(admin(), body["schema"], body["changes"])

    @router.post("/governance/plan")
    @translate
    def governance_plan(body: dict):
        return workbench.governance_plan(admin(), body.get("trigger_field", "business_date"))

    @router.post("/optimizer/plan")
    @translate
    def optimizer(body: dict):
        return workbench.optimizer(admin(), body["rules"])

    @router.post("/index-ledger/verify")
    @translate
    def ledger_verify():
        return workbench.ledger.verify_local(admin().tenant)

    @router.get("/index-ledger")
    def ledger():
        return db.fetchall("SELECT * FROM beta_index_state WHERE tenant_id=?", (admin().tenant,))

    @router.get("/audit")
    def audit():
        who = admin()
        return db.fetchall("SELECT * FROM audit_events WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1000", (who.tenant,))

    @router.post("/dashboards")
    @translate
    def dashboard_create(body: dict):
        from ..services.catalog import now, uid
        who = admin()
        did = uid()
        db.execute("INSERT INTO beta_dashboards VALUES(?,?,?,?,?)", (did, who.tenant, body["name"], db.dumps(body["config"]), now()))
        return {"id": did}

    @router.get("/dashboards")
    def dashboards():
        return db.fetchall("SELECT * FROM beta_dashboards WHERE tenant_id=?", (identity().tenant,))

    def tick():
        hcp.tick()
        archive.tick()
        for row in db.fetchall("SELECT * FROM beta_jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 10"):
            snap = db.loads(row["request_json"], {})["identity"]
            workbench.run_job(Identity(snap["tenant"], snap["actor"], tuple(snap["roles"]), tuple(snap["groups"])), row["id"])
        for row in db.fetchall("SELECT id FROM beta_jobs WHERE expires_at>0 AND expires_at<?", (__import__("time").time(),)):
            (workbench.root / (row["id"] + ".zip")).unlink(missing_ok=True)

    def worker():
        while not stop.wait(2):
            try:
                tick()
            except Exception as exc:
                # No credentials/payloads in logs.
                print("[beta-worker] tick failed:", type(exc).__name__, flush=True)

    @app.on_event("startup")
    def start():
        db.init_schema()
        enterprise.init_schema()
        archive.init_schema()
        workbench.init_schema()
        hcp.init_schema()
        workbench.bootstrap_projections(settings.default_tenant)
        if settings.demo_mode and not archive.profiles(settings.default_tenant):
            group = db.fetchone("SELECT id FROM catalogue_groups WHERE tenant_id=? ORDER BY created_at LIMIT 1", (settings.default_tenant,))
            if group:
                profile = archive.create_profile(settings.default_tenant, "Demo document archive", {
                    "destination_group_id": group["id"], "source": {"kind": "API"},
                    "mapping": {"record_type": {"constant": "INVOICE"}, "business_date": {"type": "date", "timezone": "UTC"}}})
                with tempfile.NamedTemporaryFile(dir=archive.root) as example:
                    example.write(b"Example invoice for AMP archiving. Contact billing@example.org.")
                    example.flush()
                    archive.submit(settings.default_tenant, profile["id"], example.name, "example-invoice.txt", "INV-DEMO-001", "1",
                        {"business_date": "2026-10-04T00:00:00+00:00", "jurisdiction": "UK"}, "demo-invoice-v1")
        stop.clear()
        if os.getenv("AMP_WORKER_ENABLED", "true").lower() == "true":
            thread = threading.Thread(target=worker, daemon=True, name="amp-archive-worker")
            app.state.beta_worker = thread
            thread.start()

    @app.on_event("shutdown")
    def shutdown():
        stop.set()
        if hasattr(app.state, "beta_worker"):
            app.state.beta_worker.join(timeout=3)

    app.include_router(router)
    return archive, workbench
