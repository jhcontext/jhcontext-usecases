"""B4: MCP tool dispatch — in-process call_tool() bypassing stdio."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import statistics

from jhcontext.server.mcp_server import create_mcp_server
from mcp.types import CallToolRequest, CallToolRequestParams

from .config import ITERATIONS, WARMUP
from .helpers import build_healthcare_envelope


def _make_request(name: str, arguments: dict) -> CallToolRequest:
    return CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=arguments),
    )


def run() -> dict:
    print("  [B4] MCP tool dispatch...")

    env_h, prov_h = build_healthcare_envelope()
    env_json_str = json.dumps(env_h.to_jsonld())
    turtle_h = prov_h.serialize("turtle")

    tmp = tempfile.mkdtemp(prefix="jhctx_b4_")
    server = create_mcp_server(db_path=f"{tmp}/bench.db")
    handler = server.request_handlers[CallToolRequest]

    # Run all MCP benchmarks in a single async function to avoid event loop issues
    async def run_all():
        results: dict = {}

        # submit_envelope
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("submit_envelope", {"envelope_json": env_json_str})
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["submit_envelope"] = _stats(times)

        # get_envelope
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("get_envelope", {"context_id": env_h.context_id})
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["get_envelope"] = _stats(times)

        # submit_prov_graph
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("submit_prov_graph", {
                "context_id": env_h.context_id,
                "graph_turtle": turtle_h,
            })
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["submit_prov_graph"] = _stats(times)

        # query_provenance — causal_chain
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("query_provenance", {
                "context_id": env_h.context_id,
                "query_type": "causal_chain",
                "entity_id": "art-final-decision",
            })
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["query_causal_chain"] = _stats(times)

        # query_provenance — temporal_sequence
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("query_provenance", {
                "context_id": env_h.context_id,
                "query_type": "temporal_sequence",
            })
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["query_temporal_sequence"] = _stats(times)

        # query_provenance — used_entities
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("query_provenance", {
                "context_id": env_h.context_id,
                "query_type": "used_entities",
                "entity_id": "ai-recommendation",
            })
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["query_used_entities"] = _stats(times)

        # run_audit
        times = []
        for i in range(WARMUP + ITERATIONS):
            req = _make_request("run_audit", {
                "context_id": env_h.context_id,
                "checks": ["integrity"],
            })
            t0 = time.perf_counter()
            await handler(req)
            elapsed = (time.perf_counter() - t0) * 1000
            if i >= WARMUP:
                times.append(elapsed)
        results["run_audit"] = _stats(times)

        return results

    return asyncio.run(run_all())


def _stats(times: list[float]) -> dict:
    times.sort()
    return {
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "std": statistics.stdev(times) if len(times) > 1 else 0.0,
        "min": times[0],
        "max": times[-1],
        "p95": times[int(len(times) * 0.95)],
        "p99": times[int(len(times) * 0.99)],
        "n": len(times),
    }
