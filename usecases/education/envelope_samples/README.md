# Education Envelope Samples

Static JSON envelopes that mirror the canonical figures used in the education-scenario documentation. Each file is a literal envelope readers can inspect, copy, or load without re-running a script.

| File | Scenario |
|---|---|
| `rubric_feedback_envelope_sample.json` | Scenario B — Rubric-Grounded LLM Feedback |

## Scope

Each sample is **deliberately minimal**:

- One representative `Interpretation`-group statement (rubric-criterion binding + evidence span) plus one `Observation`-group statement (rubric version), so the load-bearing fields the scenario discusses (`auxiliary=addresses`, `explanation.evidence`, `passed_artifact_pointer`, `feature_suppression`, the `proof` and `provenance_ref` blocks) are all visible.
- All other per-feedback-sentence statements are elided. The full eight-sentence runnable equivalent — built from the same fixture and emitted by `EnvelopeBuilder` — lives in [`../scenario_b.py`](../scenario_b.py); its output goes to `output/education_scenario_b_envelope.json` after `python -m usecases.education.scenario_b`.
- Hashes and identifiers are abbreviated (`sha256:a1...`, `ctx-8f3a1b2c-4d5e-...`) to match the printed sample verbatim.

## Layout

The sample keeps fields **flat at the top level** for prose readability. The v0.5 canonical schema (see [`jhcontext-protocol/README.md`](https://github.com/jhdarosa/jhcontext-protocol/blob/main/README.md)) groups privacy and compliance fields under nested `privacy` / `compliance` blocks; `scenario_b.py` produces the spec-conformant nested form.

## Use

These files are intended as a copy-paste reference. They are **not** loaded by any test or pipeline in this repo — they would fail integrity verification (`verify_integrity`) because the proof block carries placeholder values, by design. To inspect a real signed envelope with a verifiable proof, run `scenario_b.py` and load `output/education_scenario_b_envelope.json`.
