"""B7: Compliance package export — end-to-end via REST API."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile

from fastapi.testclient import TestClient

from jhcontext.server.app import create_app

from .config import ITERATIONS, WARMUP
from .helpers import timed, build_healthcare_envelope


def run() -> dict:
    print("  [B7] Compliance package export...")

    results: dict = {}
    env_h, prov_h = build_healthcare_envelope()
    env_dict = env_h.to_jsonld()
    turtle_h = prov_h.serialize("turtle")

    tmp = tempfile.mkdtemp(prefix="jhctx_b7_")
    app = create_app(db_path=f"{tmp}/bench.db")

    with TestClient(app) as client:
        # Seed data
        resp = client.post("/envelopes", json={"envelope": env_dict})
        ctx_id = resp.json()["context_id"]
        client.post("/provenance", json={"context_id": ctx_id, "graph_turtle": turtle_h})

        # Benchmark compliance export
        def export_package():
            return client.get(f"/compliance/package/{ctx_id}")

        results["export_time"] = timed(export_package, ITERATIONS, WARMUP)

        # Verify package contents (single run)
        resp = client.get(f"/compliance/package/{ctx_id}")
        assert resp.status_code == 200

        buf = io.BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            results["zip_contents"] = names
            results["zip_size_bytes"] = len(resp.content)

            # Verify all required files present
            required = ["envelope.json", "audit_report.json", "manifest.json", "provenance.ttl"]
            results["all_files_present"] = all(f in names for f in required)

            # Read and verify audit report
            audit_data = json.loads(zf.read("audit_report.json"))
            results["audit_passed"] = audit_data.get("overall_passed", False)

            # Read manifest
            manifest = json.loads(zf.read("manifest.json"))
            results["manifest_context_id"] = manifest.get("context_id")
            results["manifest_has_hash"] = manifest.get("envelope_hash") is not None

    return results
