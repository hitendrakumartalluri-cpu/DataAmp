from __future__ import annotations

from typing import Any

from ..db import Database
from .catalog import CatalogService
from .managed_objects import ManagedObjectService


class HCPGatewayService:
    """Controlled HCP REST compatibility adapter over the canonical object service."""
    def __init__(self, db: Database, catalog: CatalogService):
        self.objects = ManagedObjectService(db, catalog)

    def put_object(self, tenant: str, namespace: str, object_path: str, data: bytes,
                   content_type: str = "application/octet-stream") -> dict[str, Any]:
        return self.objects.put_object(tenant, namespace, object_path, data, content_type, protocol="HCP_REST")

    def get_object(self, tenant: str, namespace: str, object_path: str, version_id: str = ""):
        return self.objects.get_object(tenant, namespace, object_path, version_id)

    def head_object(self, tenant: str, namespace: str, object_path: str, version_id: str = ""):
        return self.objects.head_object(tenant, namespace, object_path, version_id)

    def delete_object(self, tenant: str, namespace: str, object_path: str):
        return self.objects.delete_object(tenant, namespace, object_path, protocol="HCP_REST")

    def put_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str,
                       data: bytes, content_type: str = "application/octet-stream"):
        return self.objects.put_annotation(tenant, namespace, object_path, annotation_name, data, content_type)

    def get_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str, payload_version_id: str = ""):
        return self.objects.get_annotation(tenant, namespace, object_path, annotation_name, payload_version_id)

    def head_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str, payload_version_id: str = ""):
        return self.objects.head_annotation(tenant, namespace, object_path, annotation_name, payload_version_id)

    def delete_annotation(self, tenant: str, namespace: str, object_path: str, annotation_name: str):
        return self.objects.delete_annotation(tenant, namespace, object_path, annotation_name)
