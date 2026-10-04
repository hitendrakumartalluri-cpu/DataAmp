from __future__ import annotations
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx


def run_pipeline(name, parameters):
    """Admin-configured pipeline contract; never accept arbitrary server paths/URLs."""
    allowed = json.loads(os.getenv("AMP_HOP_PIPELINES_JSON") or "{}")
    profile = allowed.get(name)
    if not profile or profile.get("mode") != "READ_ONLY":
        raise ValueError("configure a qualified READ_ONLY Hop pipeline before execution")
    base = os.getenv("AMP_HOP_URL", "").rstrip("/")
    if not base:
        raise ValueError("AMP_HOP_URL is not configured")
    auth = None
    if os.getenv("AMP_HOP_USERNAME"):
        auth = (os.environ["AMP_HOP_USERNAME"], os.getenv("AMP_HOP_PASSWORD", ""))
    response = httpx.get(base + "/hop/execPipeline", params={"pipeline": profile["path"], "level": "Minimal", **parameters}, auth=auth, timeout=120)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    if root.findtext("result") != "OK":
        raise ValueError("Hop pipeline reported an execution failure")
    return {"mode": "EXTERNAL_HOP", "pipeline": name, "status": "EXECUTION_ACKNOWLEDGED",
        "native_mutation": False, "id": root.findtext("id", "")}
