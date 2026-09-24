from pathlib import Path
import tempfile

from app.db import Database
from app.services.catalog import CatalogService
from app.services.events import EventService
from app.services.operations import OperationsService
from app.services.processing import ProcessingService


def stack(name: str):
    root = Path(tempfile.mkdtemp(prefix=f"amp-{name}-"))
    db = Database(f"sqlite:///{root / 'amp.db'}")
    db.init_schema()
    catalog = CatalogService(db)
    return root, db, catalog, OperationsService(db, catalog), ProcessingService(db, catalog)


def local_scope(catalog: CatalogService, root: Path, tenant: str = "test"):
    storage = catalog.create_storage({
        "tenant_id": tenant, "name": "Connector fixture", "kind": "LOCAL",
        "role": "EXTERNAL", "root_path": str(root),
    })
    scope = catalog.create_catalogue_group(
        tenant=tenant, storage_id=storage["id"], container_name=".",
        container_type="DIRECTORY", name="Fixture scope", physical_shards=2,
    )
    return storage, scope


def test_connector_discovery_and_indexing_are_read_only_to_source():
    root, _db, catalog, operations, processing = stack("discovery")
    source = root / "source"
    source.mkdir()
    (source / "contract.txt").write_text("Customer contract jurisdiction UK")
    _storage, scope = local_scope(catalog, source)
    discovered = operations.discover("test", scope["id"])
    indexed = processing.index_source(scope["id"])
    assert discovered["registered"] == 1
    assert indexed["indexed"] == 1
    assert (source / "contract.txt").read_text() == "Customer contract jurisdiction UK"


def test_generation_marks_missing_then_tombstones():
    root, _db, catalog, operations, _processing = stack("generation")
    source = root / "source"
    source.mkdir()
    item = source / "record.txt"
    item.write_text("record")
    _storage, scope = local_scope(catalog, source)
    operations.discover("test", scope["id"])
    item.unlink()
    first = operations.discover("test", scope["id"])
    second = operations.discover("test", scope["id"])
    assert first["missing"] == 1
    assert second["tombstoned"] == 1


def test_event_capture_deduplicates_storage_changes():
    root, db, catalog, _operations, _processing = stack("events")
    source = root / "source"
    source.mkdir()
    (source / "record.txt").write_text("event record")
    _storage, scope = local_scope(catalog, source)
    events = EventService(db, catalog)
    payload = {
        "tenant_id": "test", "catalogue_group_id": scope["id"],
        "source_type": "AWS_S3", "event_type": "OBJECT_CREATED",
        "object_key": "record.txt", "native_event_id": "evt-1", "size_bytes": 12,
    }
    first = events.apply_event(payload)
    second = events.apply_event(payload)
    assert first["status"] == "APPLIED"
    assert second["status"] == "DUPLICATE"


def test_index_reconciliation_detects_missing_projection():
    root, _db, catalog, operations, _processing = stack("reconcile")
    source = root / "source"
    source.mkdir()
    (source / "record.txt").write_text("record")
    _storage, scope = local_scope(catalog, source)
    operations.discover("test", scope["id"])
    result = operations.reconcile("test", scope["id"], target="INDEX")
    assert result["findings"] == 1
    assert result["finding_counts"]["MISSING_FROM_INDEX"] == 1
