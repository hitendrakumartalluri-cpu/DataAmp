CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS storage_systems (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'EXTERNAL', endpoint TEXT, root_path TEXT, region TEXT,
  access_key TEXT, secret_key TEXT, options_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'ONLINE', created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS catalogue_groups (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, storage_id UUID NOT NULL REFERENCES storage_systems(id),
  name TEXT NOT NULL, container_type TEXT NOT NULL, container_name TEXT NOT NULL,
  recon_namespace UUID NOT NULL, virtual_shard_count INTEGER NOT NULL DEFAULT 1024,
  physical_shard_count INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'ACTIVE',
  package_placement_mode TEXT NOT NULL DEFAULT 'AMP_MANAGED_HASH',
  package_root_prefix TEXT NOT NULL DEFAULT '.amp/objects',
  package_hash_levels INTEGER NOT NULL DEFAULT 2,
  package_hash_segment_chars INTEGER NOT NULL DEFAULT 2,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(storage_id, container_type, container_name)
);

CREATE TABLE IF NOT EXISTS catalogue_shards (
  id UUID PRIMARY KEY, catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  shard_no INTEGER NOT NULL, virtual_start INTEGER NOT NULL, virtual_end INTEGER NOT NULL,
  physical_database TEXT NOT NULL DEFAULT 'amp_control', physical_schema TEXT,
  state TEXT NOT NULL DEFAULT 'ACTIVE', created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(catalogue_group_id, shard_no)
);

CREATE TABLE IF NOT EXISTS jobs (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, job_type TEXT NOT NULL, status TEXT NOT NULL,
  progress DOUBLE PRECISION NOT NULL DEFAULT 0, catalogue_group_id UUID, shard_id UUID,
  source_group_id UUID, target_group_id UUID,
  selector_json JSONB NOT NULL DEFAULT '{}'::jsonb, metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT, created_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS catalogue_generations (
  id UUID PRIMARY KEY, catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  job_id UUID REFERENCES jobs(id) ON DELETE SET NULL, generation_no INTEGER NOT NULL,
  status TEXT NOT NULL, scan_prefix TEXT NOT NULL DEFAULT '', objects_seen BIGINT NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ,
  UNIQUE(catalogue_group_id, generation_no)
);

CREATE TABLE IF NOT EXISTS catalogue_objects (
  id UUID PRIMARY KEY, catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  shard_id UUID NOT NULL REFERENCES catalogue_shards(id), recon_id UUID NOT NULL,
  virtual_shard INTEGER NOT NULL, object_key TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '',
  logical_name TEXT, storage_layout TEXT NOT NULL DEFAULT 'DIRECT', package_root TEXT, payload_key TEXT, manifest_key TEXT,
  content_type TEXT, size_bytes BIGINT NOT NULL DEFAULT 0, etag TEXT,
  checksum_sha256 TEXT, lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE', missing_count INTEGER NOT NULL DEFAULT 0,
  source_mode TEXT NOT NULL DEFAULT 'DISCOVERED', origin_recon_id UUID,
  first_seen_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL,
  last_seen_generation_id UUID, updated_at TIMESTAMPTZ NOT NULL, tombstoned_at TIMESTAMPTZ,
  UNIQUE(catalogue_group_id, object_key), UNIQUE(catalogue_group_id, recon_id)
);

CREATE TABLE IF NOT EXISTS metadata_records (
  id UUID PRIMARY KEY, catalogue_object_id UUID NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
  source TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT 'business', key TEXT NOT NULL,
  value_text TEXT, value_json JSONB, authoritative BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(catalogue_object_id, source, namespace, key)
);


CREATE TABLE IF NOT EXISTS gateway_routes (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, namespace TEXT NOT NULL,
  catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  object_prefix TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'ACTIVE',
  response_mode TEXT NOT NULL DEFAULT 'AMP_NORMALIZED',
  backend_header_policy TEXT NOT NULL DEFAULT 'SELECTED',
  add_amp_request_id BOOLEAN NOT NULL DEFAULT TRUE,
  capture_backend_response BOOLEAN NOT NULL DEFAULT TRUE,
  max_captured_error_body_bytes INTEGER NOT NULL DEFAULT 65536,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(tenant_id, namespace)
);

CREATE TABLE IF NOT EXISTS object_annotations (
  id UUID PRIMARY KEY, catalogue_object_id UUID NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
  annotation_name TEXT NOT NULL, sidecar_key TEXT NOT NULL, content_type TEXT NOT NULL,
  size_bytes BIGINT NOT NULL DEFAULT 0, checksum_sha256 TEXT NOT NULL, annotation_version INTEGER NOT NULL DEFAULT 1,
  native_version_id TEXT NOT NULL DEFAULT '', payload_native_version_id TEXT NOT NULL DEFAULT '', backend_response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  state TEXT NOT NULL DEFAULT 'ACTIVE', created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(catalogue_object_id, annotation_name)
);

CREATE TABLE IF NOT EXISTS catalogue_object_versions (
  id UUID PRIMARY KEY, catalogue_object_id UUID NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
  version_recon_id UUID NOT NULL, native_version_id TEXT NOT NULL DEFAULT '',
  etag TEXT, checksum_sha256 TEXT, size_bytes BIGINT NOT NULL DEFAULT 0, content_type TEXT,
  is_current BOOLEAN NOT NULL DEFAULT TRUE, observed_at TIMESTAMPTZ NOT NULL, backend_response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(catalogue_object_id, native_version_id)
);

CREATE TABLE IF NOT EXISTS object_annotation_versions (
  id UUID PRIMARY KEY, annotation_id UUID NOT NULL REFERENCES object_annotations(id) ON DELETE CASCADE,
  native_version_id TEXT NOT NULL DEFAULT '', payload_native_version_id TEXT NOT NULL DEFAULT '', checksum_sha256 TEXT, size_bytes BIGINT NOT NULL DEFAULT 0,
  content_type TEXT, is_current BOOLEAN NOT NULL DEFAULT TRUE, observed_at TIMESTAMPTZ NOT NULL,
  backend_response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(annotation_id, native_version_id)
);

CREATE TABLE IF NOT EXISTS object_version_annotation_links (
  id UUID PRIMARY KEY, catalogue_object_id UUID NOT NULL REFERENCES catalogue_objects(id) ON DELETE CASCADE,
  payload_native_version_id TEXT NOT NULL, annotation_name TEXT NOT NULL, annotation_native_version_id TEXT NOT NULL DEFAULT '',
  linked_at TIMESTAMPTZ NOT NULL,
  UNIQUE(catalogue_object_id, payload_native_version_id, annotation_name)
);

CREATE TABLE IF NOT EXISTS backend_transactions (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, gateway_route_id UUID, catalogue_group_id UUID,
  object_id UUID, request_id TEXT, protocol TEXT NOT NULL, operation TEXT NOT NULL, logical_key TEXT,
  backend_kind TEXT, backend_status INTEGER, backend_code TEXT, outcome TEXT NOT NULL,
  response_headers_json JSONB NOT NULL DEFAULT '{}'::jsonb, raw_response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_body TEXT, created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_results (
  id UUID PRIMARY KEY, job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  catalogue_group_id UUID NOT NULL, shard_id UUID, catalogue_object_id UUID, recon_id UUID,
  target_type TEXT NOT NULL, finding_type TEXT NOT NULL, severity TEXT NOT NULL,
  details_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_links (
  id UUID PRIMARY KEY, job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
  source_group_id UUID NOT NULL, source_recon_id UUID NOT NULL,
  target_group_id UUID NOT NULL, target_recon_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(source_group_id, source_recon_id, target_group_id, target_recon_id)
);

/* Local beta search/AI simulator. Production routes these records to Solr/vector services. */
CREATE TABLE IF NOT EXISTS search_documents (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, source_id UUID NOT NULL,
  container_type TEXT NOT NULL, container_name TEXT NOT NULL, recon_id UUID NOT NULL,
  source_version TEXT NOT NULL DEFAULT '', object_key TEXT NOT NULL, content_hash TEXT,
  chunk_seq INTEGER NOT NULL, text_content TEXT NOT NULL, text_hash TEXT NOT NULL,
  embedding_json JSONB, pipeline_version TEXT NOT NULL, indexed_at TIMESTAMPTZ NOT NULL,
  UNIQUE(source_id, container_type, container_name, recon_id, chunk_seq)
);

CREATE TABLE IF NOT EXISTS ai_artifacts (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, source_id UUID NOT NULL,
  container_type TEXT NOT NULL, container_name TEXT NOT NULL, recon_id UUID NOT NULL,
  source_content_hash TEXT, artifact_type TEXT NOT NULL, model_name TEXT NOT NULL,
  model_version TEXT NOT NULL, pipeline_version TEXT NOT NULL, status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(source_id, container_type, container_name, recon_id, artifact_type, model_name, model_version)
);

CREATE TABLE IF NOT EXISTS datasets (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT,
  membership_mode TEXT NOT NULL DEFAULT 'DYNAMIC', query_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_members (
  dataset_id UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  source_id UUID NOT NULL, container_type TEXT NOT NULL, container_name TEXT NOT NULL,
  recon_id UUID NOT NULL, added_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(dataset_id, source_id, container_type, container_name, recon_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id UUID,
  actor TEXT NOT NULL, action TEXT NOT NULL, catalogue_object_id UUID, recon_id UUID,
  request_id TEXT, outcome TEXT NOT NULL, details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox_events (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id UUID,
  aggregate_type TEXT NOT NULL, aggregate_id UUID NOT NULL, event_type TEXT NOT NULL,
  payload_json JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ
);


CREATE TABLE IF NOT EXISTS change_capture_configs (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id UUID NOT NULL UNIQUE REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  mode TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE, config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  checkpoint_json JSONB NOT NULL DEFAULT '{}'::jsonb, status TEXT NOT NULL DEFAULT 'CONFIGURED', consumer_lag BIGINT NOT NULL DEFAULT 0,
  last_source_event_at TIMESTAMPTZ, last_applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_events (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL, native_event_id TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL, object_key TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '', event_time TIMESTAMPTZ NOT NULL,
  sequencer TEXT NOT NULL DEFAULT '', payload_json JSONB NOT NULL DEFAULT '{}'::jsonb, status TEXT NOT NULL,
  kafka_topic TEXT, kafka_partition INTEGER, kafka_offset BIGINT, error_message TEXT,
  received_at TIMESTAMPTZ NOT NULL, applied_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS event_dead_letters (
  id UUID PRIMARY KEY, storage_event_id UUID REFERENCES storage_events(id) ON DELETE CASCADE,
  catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  payload_json JSONB NOT NULL, error_message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
  created_at TIMESTAMPTZ NOT NULL, replayed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS event_object_watermarks (
  catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  object_key TEXT NOT NULL, source_type TEXT NOT NULL, last_sequencer TEXT NOT NULL DEFAULT '',
  last_event_time TIMESTAMPTZ, last_native_event_id TEXT, updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(catalogue_group_id, object_key, source_type)
);

CREATE TABLE IF NOT EXISTS catalogue_schedules (
  id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, catalogue_group_id UUID NOT NULL REFERENCES catalogue_groups(id) ON DELETE CASCADE,
  schedule_type TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE, interval_minutes INTEGER NOT NULL,
  next_run_at TIMESTAMPTZ, last_run_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(catalogue_group_id, schedule_type)
);

CREATE INDEX IF NOT EXISTS idx_capture_tenant ON change_capture_configs(tenant_id, enabled, status);
CREATE INDEX IF NOT EXISTS idx_storage_events_group ON storage_events(catalogue_group_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_storage_events_status ON storage_events(status, received_at);
CREATE INDEX IF NOT EXISTS idx_event_dlq_group ON event_dead_letters(catalogue_group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_watermarks_group ON event_object_watermarks(catalogue_group_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON catalogue_schedules(enabled, next_run_at);


CREATE INDEX IF NOT EXISTS idx_gateway_routes_tenant ON gateway_routes(tenant_id, state, namespace);
CREATE INDEX IF NOT EXISTS idx_annotations_object ON object_annotations(catalogue_object_id, state, annotation_name);

ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS storage_layout TEXT NOT NULL DEFAULT 'DIRECT';
ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS package_root TEXT;
ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS payload_key TEXT;
ALTER TABLE catalogue_objects ADD COLUMN IF NOT EXISTS manifest_key TEXT;
UPDATE catalogue_objects SET payload_key=object_key WHERE payload_key IS NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_objects_payload_key ON catalogue_objects(catalogue_group_id, payload_key);
CREATE INDEX IF NOT EXISTS idx_catalog_objects_package_root ON catalogue_objects(catalogue_group_id, package_root);

CREATE INDEX IF NOT EXISTS idx_groups_tenant ON catalogue_groups(tenant_id, state);
CREATE INDEX IF NOT EXISTS idx_shards_group ON catalogue_shards(catalogue_group_id, shard_no);
CREATE INDEX IF NOT EXISTS idx_catalog_objects_group ON catalogue_objects(catalogue_group_id, lifecycle_state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_objects_shard ON catalogue_objects(shard_id, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_catalog_objects_recon ON catalogue_objects(recon_id);
CREATE INDEX IF NOT EXISTS idx_jobs_group ON jobs(catalogue_group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_group ON reconciliation_results(catalogue_group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_recon ON search_documents(source_id, container_name, recon_id);
CREATE INDEX IF NOT EXISTS idx_ai_recon ON ai_artifacts(source_id, container_name, recon_id);
CREATE INDEX IF NOT EXISTS idx_audit_group ON audit_events(catalogue_group_id, created_at DESC);


-- beta.5 response/version observation additions for existing databases
ALTER TABLE gateway_routes ADD COLUMN IF NOT EXISTS response_mode TEXT NOT NULL DEFAULT 'AMP_NORMALIZED';
ALTER TABLE gateway_routes ADD COLUMN IF NOT EXISTS backend_header_policy TEXT NOT NULL DEFAULT 'SELECTED';
ALTER TABLE gateway_routes ADD COLUMN IF NOT EXISTS add_amp_request_id BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE gateway_routes ADD COLUMN IF NOT EXISTS capture_backend_response BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE gateway_routes ADD COLUMN IF NOT EXISTS max_captured_error_body_bytes INTEGER NOT NULL DEFAULT 65536;
ALTER TABLE object_annotations ADD COLUMN IF NOT EXISTS native_version_id TEXT NOT NULL DEFAULT '';
ALTER TABLE object_annotations ADD COLUMN IF NOT EXISTS backend_response_json JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_object_versions_object ON catalogue_object_versions(catalogue_object_id,is_current,observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_annotation_versions_annotation ON object_annotation_versions(annotation_id,is_current,observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_version_annotation_links ON object_version_annotation_links(catalogue_object_id,payload_native_version_id,annotation_name);
CREATE INDEX IF NOT EXISTS idx_backend_transactions_route ON backend_transactions(gateway_route_id,created_at DESC);

ALTER TABLE object_annotations ADD COLUMN IF NOT EXISTS payload_native_version_id TEXT NOT NULL DEFAULT '';
ALTER TABLE object_annotation_versions ADD COLUMN IF NOT EXISTS payload_native_version_id TEXT NOT NULL DEFAULT '';
