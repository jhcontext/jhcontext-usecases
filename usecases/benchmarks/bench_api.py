"""B3: REST API round-trip — full FastAPI stack via TestClient."""

from __future__ import annotations

import json
import tempfile

from fastapi.testclient import TestClient

from jhcontext.server.app import create_app

from .config import ITERATIONS, WARMUP
from .helpers import timed, build_healthcare_envelope, build_education_envelope


def run() -> dict:
    print("  [B3] REST API round-trip...")

    results: dict = {}
    env_h, prov_h = build_healthcare_envelope()
    env_e, prov_g, _ = build_education_envelope()

    env_h_dict = env_h.to_jsonld()
    turtle_h = prov_h.serialize("turtle")
    env_e_dict = env_e.to_jsonld()
    turtle_g = prov_g.serialize("turtle")

    tmp = tempfile.mkdtemp(prefix="jhctx_b3_")
    app = create_app(db_path=f"{tmp}/bench.db")

    with TestClient(app) as client:
        # POST envelope
        def post_envelope():
            client.post("/envelopes", json={"envelope": env_h_dict})

        results["post_envelope"] = timed(post_envelope, ITERATIONS, WARMUP)

        # Seed data for GET benchmarks
        resp = client.post("/envelopes", json={"envelope": env_h_dict})
        ctx_id = resp.json()["context_id"]

        # GET envelope
        results["get_envelope"] = timed(
            lambda: client.get(f"/envelopes/{ctx_id}"), ITERATIONS, WARMUP
        )

        # POST provenance
        def post_prov():
            client.post("/provenance", json={
                "context_id": ctx_id,
                "graph_turtle": turtle_h,
            })

        results["post_provenance"] = timed(post_prov, ITERATIONS, WARMUP)

        # Seed provenance
        client.post("/provenance", json={"context_id": ctx_id, "graph_turtle": turtle_h})

        # Query provenance — causal chain
        results["query_causal_chain"] = timed(
            lambda: client.post("/provenance/query", json={
                "context_id": ctx_id,
                "query_type": "causal_chain",
                "entity_id": "art-final-decision",
            }),
            ITERATIONS, WARMUP,
        )

        # Query provenance — temporal sequence
        results["query_temporal_sequence"] = timed(
            lambda: client.post("/provenance/query", json={
                "context_id": ctx_id,
                "query_type": "temporal_sequence",
            }),
            ITERATIONS, WARMUP,
        )

        # Query provenance — used entities
        results["query_used_entities"] = timed(
            lambda: client.post("/provenance/query", json={
                "context_id": ctx_id,
                "query_type": "used_entities",
                "entity_id": "ai-recommendation",
            }),
            ITERATIONS, WARMUP,
        )

        # GET compliance package
        results["compliance_package"] = timed(
            lambda: client.get(f"/compliance/package/{ctx_id}"),
            ITERATIONS, WARMUP,
        )

        # LIST envelopes
        results["list_envelopes"] = timed(
            lambda: client.get("/envelopes"), ITERATIONS, WARMUP
        )

        # Full healthcare round-trip (post envelope + prov, query, get package)
        def full_healthcare():
            r = client.post("/envelopes", json={"envelope": env_h_dict})
            cid = r.json()["context_id"]
            client.post("/provenance", json={"context_id": cid, "graph_turtle": turtle_h})
            client.post("/provenance/query", json={
                "context_id": cid, "query_type": "temporal_sequence",
            })
            client.get(f"/compliance/package/{cid}")

        results["full_healthcare_api"] = timed(full_healthcare, ITERATIONS, WARMUP)

    return results
