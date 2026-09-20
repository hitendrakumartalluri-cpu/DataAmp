from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


class Database:
    """Small DB abstraction: SQLite for zero-dependency beta runs, PostgreSQL in container/pilot mode."""

    def __init__(self, url: str):
        self.url = url
        self.is_postgres = url.startswith("postgresql://") or url.startswith("postgres://")
        self._lock = threading.RLock()
        if self.is_postgres:
            try:
                import psycopg  # type: ignore
                from psycopg.rows import dict_row  # type: ignore
            except ImportError as exc:
                raise RuntimeError("PostgreSQL mode requires psycopg[binary]. Install services/control-plane/requirements.txt") from exc
            self.psycopg = psycopg
            self.dict_row = dict_row
            self.path = None
        else:
            path = url.replace("sqlite:///", "", 1)
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self):
        if self.is_postgres:
            conn = self.psycopg.connect(self.url, autocommit=False, row_factory=self.dict_row)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock, self.connection() as conn:
            conn.execute(self._sql(sql), tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock, self.connection() as conn:
            cur = conn.cursor()
            cur.executemany(self._sql(sql), list(rows))

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(self._sql(sql), tuple(params)).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(self._sql(sql), tuple(params)).fetchall()]

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.fetchone(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    def init_schema(self) -> None:
        if self.is_postgres:
            schema_path = Path(__file__).resolve().parents[1] / "sql" / "postgres" / "001_init.sql"
            sql = schema_path.read_text()
            with self.connection() as conn:
                conn.execute(sql)
            self._ensure_catalogue_layout_columns()
            return

        schema = """
        CREATE TABLE IF NOT EXISTS storage_systems (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'EXTERNAL', endpoint TEXT, root_path TEXT, region TEXT,
          access_key TEXT, secret_key TEXT, options_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'ONLINE', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS catalogue_groups (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, storage_id TEXT NOT NULL REFERENCES storage_systems(id),
          name TEXT NOT NULL, container_type TEXT NOT NULL, container_name TEXT NOT NULL,
          recon_namespace TEXT NOT NULL, virtual_shard_count INTEGER NOT NULL DEFAULT 1024,
          physical_shard_count INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'ACTIVE',
          package_placement_mode TEXT NOT NULL DEFAULT 'AMP_MANAGED_HASH',
          package_root_prefix TEXT NOT NULL DEFAULT '.amp/objects',
          package_hash_levels INTEGER NOT NULL DEFAULT 2,
          package_hash_segment_chars INTEGER NOT NULL DEFAULT 2,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(storage_id, container_type, container_name)
        );

        CREATE TABLE IF NOT EXISTS catalogue_shards (
          id TEXT PRIMARY KEY, catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          shard_no INTEGER NOT NULL, virtual_start INTEGER NOT NULL, virtual_end INTEGER NOT NULL,
          physical_database TEXT NOT NULL DEFAULT 'amp_control', physical_schema TEXT,
          state TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(catalogue_group_id, shard_no)
        );

        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, job_type TEXT NOT NULL, status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0, catalogue_group_id TEXT, shard_id TEXT,
          source_group_id TEXT, target_group_id TEXT, selector_json TEXT NOT NULL DEFAULT '{}',
          metrics_json TEXT NOT NULL DEFAULT '{}', error_message TEXT, created_at TEXT NOT NULL,
          started_at TEXT, completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS catalogue_generations (
          id TEXT PRIMARY KEY, catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL, generation_no INTEGER NOT NULL,
          status TEXT NOT NULL, scan_prefix TEXT NOT NULL DEFAULT '', objects_seen INTEGER NOT NULL DEFAULT 0,
          started_at TEXT NOT NULL, completed_at TEXT,
          UNIQUE(catalogue_group_id, generation_no)
        );

        CREATE TABLE IF NOT EXISTS catalogue_objects (
          id TEXT PRIMARY KEY, catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          shard_id TEXT NOT NULL REFERENCES catalogue_shards(id), recon_id TEXT NOT NULL,
          virtual_shard INTEGER NOT NULL, object_key TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '',
          logical_name TEXT, storage_layout TEXT NOT NULL DEFAULT 'DIRECT', package_root TEXT, payload_key TEXT, manifest_key TEXT,
          content_type TEXT, size_bytes INTEGER NOT NULL DEFAULT 0, etag TEXT,
          checksum_sha256 TEXT, lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE', missing_count INTEGER NOT NULL DEFAULT 0,
          source_mode TEXT NOT NULL DEFAULT 'DISCOVERED', origin_recon_id TEXT,
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_seen_generation_id TEXT,
          updated_at TEXT NOT NULL, tombstoned_at TEXT,
          UNIQUE(catalogue_group_id, object_key), UNIQUE(catalogue_group_id, recon_id)
        );

        CREATE TABLE IF NOT EXISTS metadata_records (
          id TEXT PRIMARY KEY, catalogue_object_id TEXT NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
          source TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT 'business', key TEXT NOT NULL,
          value_text TEXT, value_json TEXT, authoritative INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(catalogue_object_id, source, namespace, key)
        );


        CREATE TABLE IF NOT EXISTS gateway_routes (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, namespace TEXT NOT NULL,
          catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          object_prefix TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'ACTIVE',
          response_mode TEXT NOT NULL DEFAULT 'AMP_NORMALIZED',
          backend_header_policy TEXT NOT NULL DEFAULT 'SELECTED',
          add_amp_request_id INTEGER NOT NULL DEFAULT 1,
          capture_backend_response INTEGER NOT NULL DEFAULT 1,
          max_captured_error_body_bytes INTEGER NOT NULL DEFAULT 65536,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(tenant_id, namespace)
        );

        CREATE TABLE IF NOT EXISTS object_annotations (
          id TEXT PRIMARY KEY, catalogue_object_id TEXT NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
          annotation_name TEXT NOT NULL, sidecar_key TEXT NOT NULL, content_type TEXT NOT NULL,
          size_bytes INTEGER NOT NULL DEFAULT 0, checksum_sha256 TEXT NOT NULL, annotation_version INTEGER NOT NULL DEFAULT 1,
          native_version_id TEXT NOT NULL DEFAULT '', payload_native_version_id TEXT NOT NULL DEFAULT '', backend_response_json TEXT NOT NULL DEFAULT '{}',
          state TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(catalogue_object_id, annotation_name)
        );

        CREATE TABLE IF NOT EXISTS catalogue_object_versions (
          id TEXT PRIMARY KEY, catalogue_object_id TEXT NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
          version_recon_id TEXT NOT NULL, native_version_id TEXT NOT NULL DEFAULT '',
          etag TEXT, checksum_sha256 TEXT, size_bytes INTEGER NOT NULL DEFAULT 0, content_type TEXT,
          is_current INTEGER NOT NULL DEFAULT 1, observed_at TEXT NOT NULL, backend_response_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE(catalogue_object_id, native_version_id)
        );

        CREATE TABLE IF NOT EXISTS object_annotation_versions (
          id TEXT PRIMARY KEY, annotation_id TEXT NOT NULL REFERENCES object_annotations(id) ON DELETE CASCADE,
          native_version_id TEXT NOT NULL DEFAULT '', payload_native_version_id TEXT NOT NULL DEFAULT '', checksum_sha256 TEXT, size_bytes INTEGER NOT NULL DEFAULT 0,
          content_type TEXT, is_current INTEGER NOT NULL DEFAULT 1, observed_at TEXT NOT NULL,
          backend_response_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE(annotation_id, native_version_id)
        );

        CREATE TABLE IF NOT EXISTS object_version_annotation_links (
          id TEXT PRIMARY KEY, catalogue_object_id TEXT NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
          payload_native_version_id TEXT NOT NULL, annotation_name TEXT NOT NULL, annotation_native_version_id TEXT NOT NULL DEFAULT '',
          linked_at TEXT NOT NULL,
          UNIQUE(catalogue_object_id, payload_native_version_id, annotation_name)
        );

        CREATE TABLE IF NOT EXISTS backend_transactions (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, gateway_route_id TEXT, catalogue_group_id TEXT,
          object_id TEXT, request_id TEXT, protocol TEXT NOT NULL, operation TEXT NOT NULL, logical_key TEXT,
          backend_kind TEXT, backend_status INTEGER, backend_code TEXT, outcome TEXT NOT NULL,
          response_headers_json TEXT NOT NULL DEFAULT '{}', raw_response_json TEXT NOT NULL DEFAULT '{}',
          error_body TEXT, created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reconciliation_results (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          catalogue_group_id TEXT NOT NULL, shard_id TEXT, catalogue_object_id TEXT, recon_id TEXT,
          target_type TEXT NOT NULL, finding_type TEXT NOT NULL, severity TEXT NOT NULL,
          details_json TEXT NOT NULL, created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS migration_links (
          id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
          source_group_id TEXT NOT NULL, source_recon_id TEXT NOT NULL,
          target_group_id TEXT NOT NULL, target_recon_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(source_group_id, source_recon_id, target_group_id, target_recon_id)
        );

        /* Local beta search/AI simulator. Production routes these records to Solr/vector services. */
        CREATE TABLE IF NOT EXISTS search_documents (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, source_id TEXT NOT NULL,
          container_type TEXT NOT NULL, container_name TEXT NOT NULL, recon_id TEXT NOT NULL,
          source_version TEXT NOT NULL DEFAULT '', object_key TEXT NOT NULL, content_hash TEXT,
          chunk_seq INTEGER NOT NULL, text_content TEXT NOT NULL, text_hash TEXT NOT NULL,
          embedding_json TEXT, pipeline_version TEXT NOT NULL, indexed_at TEXT NOT NULL,
          UNIQUE(source_id, container_type, container_name, recon_id, chunk_seq)
        );

        CREATE TABLE IF NOT EXISTS ai_artifacts (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, source_id TEXT NOT NULL,
          container_type TEXT NOT NULL, container_name TEXT NOT NULL, recon_id TEXT NOT NULL,
          source_content_hash TEXT, artifact_type TEXT NOT NULL, model_name TEXT NOT NULL,
          model_version TEXT NOT NULL, pipeline_version TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(source_id, container_type, container_name, recon_id, artifact_type, model_name, model_version)
        );

        CREATE TABLE IF NOT EXISTS datasets (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT,
          membership_mode TEXT NOT NULL DEFAULT 'DYNAMIC', query_json TEXT NOT NULL DEFAULT '{}',
          version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'ACTIVE',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dataset_members (
          dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
          source_id TEXT NOT NULL, container_type TEXT NOT NULL, container_name TEXT NOT NULL,
          recon_id TEXT NOT NULL, added_at TEXT NOT NULL,
          PRIMARY KEY(dataset_id, source_id, container_type, container_name, recon_id)
        );

        CREATE TABLE IF NOT EXISTS audit_events (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id TEXT,
          actor TEXT NOT NULL, action TEXT NOT NULL, catalogue_object_id TEXT, recon_id TEXT,
          request_id TEXT, outcome TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outbox_events (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id TEXT,
          aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS change_capture_configs (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id TEXT NOT NULL UNIQUE REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          mode TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config_json TEXT NOT NULL DEFAULT '{}',
          checkpoint_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'CONFIGURED', consumer_lag INTEGER NOT NULL DEFAULT 0,
          last_source_event_at TEXT, last_applied_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS storage_events (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          source_type TEXT NOT NULL, native_event_id TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL UNIQUE,
          event_type TEXT NOT NULL, object_key TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '', event_time TEXT NOT NULL,
          sequencer TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
          kafka_topic TEXT, kafka_partition INTEGER, kafka_offset INTEGER, error_message TEXT, received_at TEXT NOT NULL, applied_at TEXT
        );

        CREATE TABLE IF NOT EXISTS event_dead_letters (
          id TEXT PRIMARY KEY, storage_event_id TEXT REFERENCES storage_events(id) ON DELETE CASCADE,
          catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          payload_json TEXT NOT NULL, error_message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
          created_at TEXT NOT NULL, replayed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS event_object_watermarks (
          catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          object_key TEXT NOT NULL, source_type TEXT NOT NULL, last_sequencer TEXT NOT NULL DEFAULT '',
          last_event_time TEXT, last_native_event_id TEXT, updated_at TEXT NOT NULL,
          PRIMARY KEY(catalogue_group_id, object_key, source_type)
        );

        CREATE TABLE IF NOT EXISTS catalogue_schedules (
          id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id TEXT NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
          schedule_type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, interval_minutes INTEGER NOT NULL,
          next_run_at TEXT, last_run_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(catalogue_group_id, schedule_type)
        );

        CREATE INDEX IF NOT EXISTS idx_capture_tenant ON change_capture_configs(tenant_id, enabled, status);
        CREATE INDEX IF NOT EXISTS idx_storage_events_group ON storage_events(catalogue_group_id, received_at);
        CREATE INDEX IF NOT EXISTS idx_storage_events_status ON storage_events(status, received_at);
        CREATE INDEX IF NOT EXISTS idx_event_dlq_group ON event_dead_letters(catalogue_group_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_event_watermarks_group ON event_object_watermarks(catalogue_group_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_schedules_due ON catalogue_schedules(enabled, next_run_at);


        CREATE INDEX IF NOT EXISTS idx_gateway_routes_tenant ON gateway_routes(tenant_id, state, namespace);
        CREATE INDEX IF NOT EXISTS idx_annotations_object ON object_annotations(catalogue_object_id, state, annotation_name);
        CREATE INDEX IF NOT EXISTS idx_version_annotation_links ON object_version_annotation_links(catalogue_object_id,payload_native_version_id,annotation_name);

        CREATE INDEX IF NOT EXISTS idx_groups_tenant ON catalogue_groups(tenant_id, state);
        CREATE INDEX IF NOT EXISTS idx_shards_group ON catalogue_shards(catalogue_group_id, shard_no);
        CREATE INDEX IF NOT EXISTS idx_catalog_objects_payload_key ON catalogue_objects(catalogue_group_id, payload_key);
        CREATE INDEX IF NOT EXISTS idx_catalog_objects_package_root ON catalogue_objects(catalogue_group_id, package_root);
        CREATE INDEX IF NOT EXISTS idx_catalog_objects_group ON catalogue_objects(catalogue_group_id, lifecycle_state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_catalog_objects_shard ON catalogue_objects(shard_id, lifecycle_state);
        CREATE INDEX IF NOT EXISTS idx_catalog_objects_recon ON catalogue_objects(recon_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_group ON jobs(catalogue_group_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_recon_group ON reconciliation_results(catalogue_group_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_search_recon ON search_documents(source_id, container_name, recon_id);
        CREATE INDEX IF NOT EXISTS idx_ai_recon ON ai_artifacts(source_id, container_name, recon_id);
        CREATE INDEX IF NOT EXISTS idx_audit_group ON audit_events(catalogue_group_id, created_at);
        """
        with self.connection() as conn:
            conn.executescript(schema)
        self._ensure_catalogue_layout_columns()

    def _ensure_catalogue_layout_columns(self) -> None:
        """Online schema evolution for beta upgrades without dropping the lab catalogue."""
        if self.is_postgres:
            statements = [
                "ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS storage_layout TEXT NOT NULL DEFAULT 'DIRECT'",
                "ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS package_root TEXT",
                "ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS payload_key TEXT",
                "ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS manifest_key TEXT",
                "UPDATE catalogue_objects SET payload_key=object_key WHERE payload_key IS NULL",
                "CREATE INDEX IF NOT EXISTS idx_catalog_objects_payload_key ON catalogue_objects(catalogue_group_id,payload_key)",
                "CREATE INDEX IF NOT EXISTS idx_catalog_objects_package_root ON catalogue_objects(catalogue_group_id,package_root)",
                "ALTER TABLE catalogue_groups ADD COLUMN IF NOT EXISTS package_placement_mode TEXT NOT NULL DEFAULT 'AMP_MANAGED_HASH'",
                "ALTER TABLE catalogue_groups ADD COLUMN IF NOT EXISTS package_root_prefix TEXT NOT NULL DEFAULT '.amp/objects'",
                "ALTER TABLE catalogue_groups ADD COLUMN IF NOT EXISTS package_hash_levels INTEGER NOT NULL DEFAULT 2",
                "ALTER TABLE catalogue_groups ADD COLUMN IF NOT EXISTS package_hash_segment_chars INTEGER NOT NULL DEFAULT 2",
            ]
            with self.connection() as conn:
                for statement in statements:
                    conn.execute(statement)
            return

        with self.connection() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(catalogue_objects)").fetchall()}
            additions = {
                "storage_layout": "TEXT NOT NULL DEFAULT 'DIRECT'",
                "package_root": "TEXT",
                "payload_key": "TEXT",
                "manifest_key": "TEXT",
            }
            for name, decl in additions.items():
                if name not in cols:
                    conn.execute(f"ALTER TABLE catalogue_objects ADD COLUMN {name} {decl}")
            conn.execute("UPDATE catalogue_objects SET payload_key=object_key WHERE payload_key IS NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_objects_payload_key ON catalogue_objects(catalogue_group_id,payload_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_objects_package_root ON catalogue_objects(catalogue_group_id,package_root)")
            group_cols = {r[1] for r in conn.execute("PRAGMA table_info(catalogue_groups)").fetchall()}
            group_add = {
                "package_placement_mode": "TEXT NOT NULL DEFAULT 'AMP_MANAGED_HASH'",
                "package_root_prefix": "TEXT NOT NULL DEFAULT '.amp/objects'",
                "package_hash_levels": "INTEGER NOT NULL DEFAULT 2",
                "package_hash_segment_chars": "INTEGER NOT NULL DEFAULT 2",
            }
            for col, ddl in group_add.items():
                if col not in group_cols:
                    conn.execute(f"ALTER TABLE catalogue_groups ADD COLUMN {col} {ddl}")

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), default=str)

    @staticmethod
    def loads(value: Any, default: Any = None) -> Any:
        # psycopg decodes PostgreSQL JSON/JSONB columns to native Python objects.
        # SQLite returns the serialized JSON string. Support both representations.
        if value is None or value == "":
            return default
        if isinstance(value, (dict, list, int, float, bool)):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            value = bytes(value).decode("utf-8")
        try:
            return json.loads(value)
        except Exception:
            return default
