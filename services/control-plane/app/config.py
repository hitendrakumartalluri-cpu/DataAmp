from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("AMP_APP_NAME", "AMP Enterprise Beta")
    version: str = os.getenv("AMP_VERSION", "0.9.0-beta.6.0-dev.1")
    database_url: str = os.getenv("AMP_DATABASE_URL", f"sqlite:///{BASE_DIR / 'amp.db'}")
    api_key: str = os.getenv("AMP_API_KEY", "")
    demo_mode: bool = os.getenv("AMP_DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}
    data_root: str = os.getenv("AMP_DATA_ROOT", str(BASE_DIR.parent / "data"))
    solr_url: str = os.getenv("AMP_SOLR_URL", "")
    solr_collection: str = os.getenv("AMP_SOLR_COLLECTION", "amp_objects")
    tika_url: str = os.getenv("AMP_TIKA_URL", "")
    hop_url: str = os.getenv("AMP_HOP_URL", "")
    default_tenant: str = os.getenv("AMP_DEFAULT_TENANT", "demo")
    max_extract_bytes: int = int(os.getenv("AMP_MAX_EXTRACT_BYTES", str(8 * 1024 * 1024)))
    chunk_chars: int = int(os.getenv("AMP_CHUNK_CHARS", "1800"))
    kafka_bootstrap: str = os.getenv("AMP_KAFKA_BOOTSTRAP", "")
    kafka_storage_topic: str = os.getenv("AMP_KAFKA_STORAGE_TOPIC", "amp.storage.changes")
    kafka_dlq_topic: str = os.getenv("AMP_KAFKA_DLQ_TOPIC", "amp.storage.changes.dlq")
    kafka_minio_raw_topic: str = os.getenv("AMP_KAFKA_MINIO_RAW_TOPIC", "amp.raw.minio")
    kafka_consumer_group: str = os.getenv("AMP_KAFKA_CONSUMER_GROUP", "amp-catalogue-events")
    s3_access_key: str = os.getenv("AMP_S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("AMP_S3_SECRET_KEY", "")
    s3_tenant: str = os.getenv("AMP_S3_TENANT", "")

settings = Settings()
