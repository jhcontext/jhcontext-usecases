# jhcontext-usecases

Compliance scenarios for the [jhcontext SDK](../jhcontext-sdk/) demonstrating EU AI Act auditability through the PAC-AI protocol.

> **TL;DR:** This is the lightweight proof-of-concept — no infrastructure, runs in-memory, and serializes envelopes/PROV graphs/audits to local files (~25ms). For the production-grade version with real CrewAI agents and AWS infrastructure (DynamoDB + S3), see [jhcontext-crewai](../jhcontext-crewai/).

## Scenarios

### Healthcare — Article 14: Meaningful Human Oversight

A hospital uses AI to recommend oncology treatment plans. The scenario proves that a physician performed genuine oversight — not just approval-clicking.

**Pipeline:** Sensor → Situation Recognition → Decision → Human Oversight → Audit

| Step | Agent | Duration | What it does |
|------|-------|----------|--------------|
| 1 | Sensor Agent | 2 min | Collects patient demographics, lab results, CT scan metadata |
| 2 | Situation Agent | 2 min | Interprets observations into clinical situation (post-op, high-risk) |
| 3 | Decision Agent | 1 min | Generates treatment recommendation (confidence: 0.87) |
| 4 | Dr. Chen | 10 min | Reviews CT scans (4m), treatment history (3m), pathology (2m), AI rec (1m), overrides |
| 5 | Audit Agent | — | Verifies temporal oversight + envelope integrity |

**Audit checks:**
- Temporal oversight: 4/4 human activities occurred after AI recommendation
- Review duration: 600s total (minimum required: 300s)
- Envelope integrity: SHA-256 hash + Ed25519 signature verified

```bash
python -m usecases.healthcare.run
```

### Education — three-scenario suite (Articles 13, 14, Annex III §3)

A university uses AI to grade essays and give per-rubric-criterion feedback, with mandatory TA oversight on summative grades. The suite has three scenarios that can be inspected independently or together; see [`usecases/education/README.md`](usecases/education/README.md) for the full scenario↔file mapping.

| Scenario | What it proves | Entry point |
|---|---|---|
| **A** — Identity-Blind Essay Grading | Negative proof + workflow isolation: identity attributes (name, ID, accommodation flags, prior grades) are absent from the grading dependency chain | `python -m usecases.education.run` |
| **B** — Rubric-Grounded LLM Feedback | Per-sentence Interpretation+Application bindings to rubric criteria with evidence spans; Semantic-Forward enforcement so downstream consumers see only `semantic_payload` (no artifact hashes / proof leak) | `python -m usecases.education.scenario_b` (single submission) or `python -m usecases.education.rubric_feedback.run` (500-submission classroom benchmark) |
| **C** — Human–AI Collaborative Grading | Temporal oversight: TA review activity recorded after AI output and before grade commit, with per-document open timestamps and configurable `min_review_seconds` | `python -m usecases.education.ta_review.run` |
| Audit (B follow-up) | Two SPARQL queries over a Scenario B envelope: rubric-criterion audit (sorted by confidence) + orphan-sentence detection | `python -m usecases.education.sparql_queries` |
| Supplementary — multimodal variant | Same A/B/C pattern over audio submissions; per-sentence interpretations bind to `(start_ms, end_ms)` audio windows audited via `verify_multimodal_binding` | `python -m usecases.education.oral_feedback.run` |

**Audit checks across the suite:** `verify_negative_proof`, `verify_workflow_isolation`, `verify_pii_detachment`, `verify_rubric_grounding`, `verify_temporal_oversight`, `verify_multimodal_binding`, `verify_integrity`.

## Output

Both scenarios write to `output/`:

| File | Description |
|------|-------------|
| `*_envelope.json` | Signed JSON-LD envelope with artifacts, decision influence, privacy, compliance |
| `*_prov.ttl` | W3C PROV graph in Turtle format (entities, activities, agents, relations) |
| `*_audit.json` | Structured audit report with pass/fail per check + evidence |
| `*_metrics.json` | Performance timing per pipeline step |

The Scenario A education runner also produces `education_equity_prov.ttl` — the isolated equity reporting workflow. Scenarios B and C produce additional artifacts (`education_scenario_b_envelope.json`, `education_scenario_b_forward.json`, `ta_review_envelope.json`, etc.); see [`usecases/education/README.md`](usecases/education/README.md) for the per-scenario output table.

## Architecture Notes

- **No persistence layer.** These scenarios run in-memory — they build envelopes and PROV graphs using the jhcontext SDK directly, then serialize to files. The jhcontext server (FastAPI + SQLite) is not used here.
- **Cryptographic signing.** Envelopes are signed with Ed25519 (via the `cryptography` package). The signature covers the canonical JSON-LD form excluding the proof block. This is real signing, not a mock — but keys are ephemeral (generated per run), not loaded from a keystore.
- **No encryption.** Artifact content hashes are included but actual artifact content (CT scans, essay text) is simulated — only hashes and metadata are stored. In production, artifacts would be encrypted at rest in the storage backend.
- **Simulated timing.** Clinical timestamps are hardcoded to demonstrate temporal ordering. No `sleep()` calls — the scenarios complete in ~25ms.

## Install

```bash
cd jhcontext-usecases
pip install -e ../jhcontext-sdk
python -m usecases.healthcare.run
python -m usecases.education.run
```

## Performance

| Metric | Healthcare | Education |
|--------|-----------|-----------|
| Envelope size | 5.8 KB | 3.6 KB |
| PROV graph size | 4.9 KB | 1.7 KB + 1.3 KB |
| PROV entities | 6 | 3 + 3 |
| PROV activities | 8 | 2 + 1 |
| Total time | ~25 ms | ~22 ms |

## Benchmarks

A 7-benchmark suite measuring protocol performance across 4 layers: in-memory, SQLite persistence, REST API (FastAPI), and MCP tool dispatch.

```bash
pip install -e "../jhcontext-sdk[all]"
pip install matplotlib  # optional, for figures
python -m usecases.benchmarks.run [--iterations 50]
```

### What it measures

| Benchmark | What | Paper value |
|-----------|------|-------------|
| B1: In-Memory | Envelope + PROV build + audit (baseline) | Overhead comparison baseline |
| B2: SQLite | Direct `SQLiteStorage` write/read | Persistence cost isolation |
| B3: REST API | Full FastAPI round-trip via TestClient | End-to-end stack overhead |
| B4: MCP | MCP tool dispatch (in-process `call_tool`) | LLM agent integration overhead |
| B5: PROV Scaling | Query perf at 10→500 entities | Scaling behavior figure |
| B6: Crypto | SHA-256, Ed25519 sign/verify isolation | Crypto negligibility claim |
| B7: Compliance | ZIP export (envelope + PROV + audit + manifest) | Regulatory package validation |

### Key results

**Operation latency across interfaces:**

| Operation | In-Memory | SQLite | REST API | MCP |
|-----------|-----------|--------|----------|-----|
| Envelope build + sign | ~4 ms | — | — | — |
| Envelope persist | — | ~100 ms | ~280 ms | ~135 ms |
| Envelope retrieve | — | <1 ms | ~1.5 ms | ~1.4 ms |
| PROV persist | — | ~150 ms | ~155 ms | ~147 ms |
| PROV query (causal) | — | — | ~5 ms | ~5 ms |
| PROV query (temporal) | — | — | ~5 ms | ~5 ms |
| Audit (integrity) | ~0.35 ms | — | — | ~6 ms |
| Compliance package | — | — | ~10 ms | — |

**PROV graph scaling:**

| Entities | Build | Serialize | Causal Chain | Temporal Seq | Dep. Chain | Size |
|----------|-------|-----------|--------------|--------------|------------|------|
| 10 | 2 ms | 3 ms | 0.05 ms | 0.1 ms | 0.4 ms | 6 KB |
| 50 | 28 ms | 21 ms | 0.3 ms | 0.8 ms | 15 ms | 33 KB |
| 100 | 42 ms | 55 ms | 0.8 ms | 1.4 ms | 65 ms | 67 KB |
| 500 | 211 ms | 258 ms | 10 ms | 7.5 ms | 1604 ms | 347 KB |

**Crypto overhead:** SHA-256 <0.01 ms (1KB), Ed25519 sign ~0.2 ms, verify ~0.2 ms — negligible vs pipeline.

### Output

```
output/benchmarks/
├── results.json        # Full raw data with metadata
├── results.csv         # Summary for LaTeX \input
├── summary.txt         # Human-readable ASCII tables
└── figures/            # Requires matplotlib
    ├── overhead_comparison.png
    ├── prov_scaling.png
    └── crypto_breakdown.png
```

### Notes

- REST API benchmarks use `fastapi.testclient.TestClient` (in-process ASGI, no network latency) for deterministic measurements.
- MCP benchmarks call `call_tool()` directly, bypassing stdio transport — isolates tool dispatch overhead.
- SQLite write latency (~100ms) is dominated by `fsync` on commit. In production, WAL mode or async writes would reduce this significantly.
- Figures require `matplotlib>=3.8`. Benchmarks work without it — ASCII tables + JSON/CSV always generated.

## License

Apache-2.0
