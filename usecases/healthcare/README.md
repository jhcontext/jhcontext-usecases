# Healthcare Scenarios

Executable reference implementations for PAC-AI under realistic healthcare workflows — covering both a canonical human-oversight scenario (oncology treatment recommendation) and three **offline-first, resource-constrained** scenarios inspired by LMIC / field-clinical deployments.

Each script builds the exact envelope + PROV graph called for by the scenario, exercises the claimed structural verifiers, and emits JSON-LD / Turtle / audit-report artifacts under `usecases/output/`.

## Scenarios

| Scenario | Script | Pipeline | Verifiers exercised |
|---|---|---|---|
| Oncology treatment oversight (Art. 14) | [`run.py`](./run.py) | sensor → situation-recognition → decision → physician oversight → audit | `verify_temporal_oversight`, `verify_pii_detachment`, `verify_integrity` |
| Rural Emergency Cardiac Triage | [`triage_rural/run.py`](./triage_rural/run.py) | physio-signal → triage → resource-allocation (3 agents, edge device, intermittent uplink, 15-min TTL) | `verify_temporal_oversight`, `verify_negative_proof`, `verify_integrity`, `verify_pii_detachment` |
| Chronic-Disease Remote Monitoring | [`chronic_monitoring/run.py`](./chronic_monitoring/run.py) | sensor-aggregation → trend-analysis → alert-generation → care-plan (4 agents, 24-h TTL, offline sync, model-version upgrade v1.2→v1.3 captured as PROV Activity) | same four |
| CHW Mental-Health Screening | [`chw_mental_health/run.py`](./chw_mental_health/run.py) | PHQ-9 interview → risk-classification → referral → supervisor-review (4 agents, CHW offline tablet, identity vault, time-to-review as auditable metric) | same four |

The `triage_rural` scenario reproduces the protocol's reference envelope listing (envelope emitted at the physiological-signal → triage handoff). The envelope fields, artifact types, confidence, and model version match the reference verbatim.

## Run

```bash
# From jhcontext-usecases/
uv venv                             # if not already
uv pip install -e ../jhcontext-sdk
source .venv/bin/activate

# Oncology oversight scenario
python -m usecases.healthcare.run

# Offline / resource-constrained scenarios
python -m usecases.healthcare.triage_rural.run
python -m usecases.healthcare.chronic_monitoring.run
python -m usecases.healthcare.chw_mental_health.run

# Or all three offline scenarios with summary metrics
python -m usecases.healthcare.run_all
```

## Outputs (per scenario)

```
output/
  <scenario>_envelope.json      # Signed JSON-LD envelope
  <scenario>_prov.ttl           # W3C PROV graph (Turtle)
  <scenario>_audit.json         # Audit report with per-check evidence
  <scenario>_metrics.json       # Per-step timing
output/healthcare_scenarios_summary.json   # run_all combined
```

## Mapping notes

- `data_category="sensitive_clinical"` (finer-grained clinical tier concept) maps to `DataCategory.SENSITIVE` in the SDK v0.3.4 enum. The finer-grained clinical tiers (`clinical`, `sensitive_clinical`, `biometric`) are a forward-looking extension not yet present in the SDK; the generic `sensitive` tier is used here.
- `scope="sustained_trajectory"` (chronic monitoring) is carried on the alert artifact via the `**metadata` kwargs of `EnvelopeBuilder.add_artifact`.
- The sub-4 ms envelope-signing overhead is anchored by [`usecases/benchmarks/bench_crypto.py`](../benchmarks/bench_crypto.py), not by these scenario scripts (which measure end-to-end per-scenario latency including PROV graph construction).

## What the scripts demonstrate

| Claim | Executed by these scripts |
|---|---|
| Envelope fields match §3.1 + reference listing | ✅ (see `triage_rural` envelope) |
| `verify_temporal_oversight` confirms human review after AI | ✅ (all scenarios PASS) |
| `verify_negative_proof` confirms excluded data never reached the decision | ✅ (all three offline scenarios PASS) |
| `verify_pii_detachment` confirms patient identifiers are separated | ✅ (all scenarios PASS) |
| Envelope persists locally, synchronises opportunistically | ⚠️ Simulated via timestamp gaps; no real offline queue |
| Predecessor-hash chain reconciles late/out-of-order envelopes | ⚠️ Single envelope per run; multi-day chain not simulated |
| Federated-learning composition | ❌ Not demonstrated (conceptual) |
| Clinical outcome improvement | ❌ Not claimed — these are *structural compliance analyses*, not empirical effectiveness claims |

These scripts are a **structural** reproduction of the scenarios (proving the protocol mechanics support each claim) — they are not a clinical evaluation. Empirical validation with real clinicians and deployment sites remains future work.
