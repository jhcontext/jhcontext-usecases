# `usecases/education/` — scenario-to-file mapping

SDK-level (no orchestration framework) reference scripts for the three
education scenarios — Identity-Blind Essay Grading (A), Rubric-Grounded
LLM Feedback (B), and Human–AI Collaborative Grading (C). Each scenario
has a single-shot variant (one envelope, fixed fixture) and, where
relevant, a classroom-scale variant (500 submissions, JSONL output) for
benchmarking.

## Scenario ↔ file map

| Scenario | Variant | File | Entry point |
|---|---|---|---|
| **A — Identity-Blind Essay Grading** | Single submission | [`run.py`](run.py) | `python -m usecases.education.run` |
| **B — Rubric-Grounded LLM Feedback** | Single submission, matches the canonical envelope sample | [`scenario_b.py`](scenario_b.py) | `python -m usecases.education.scenario_b` |
| **B — Rubric-Grounded LLM Feedback** | Classroom benchmark (500 × 8 sentences, text) | [`rubric_feedback/run.py`](rubric_feedback/run.py) | `python -m usecases.education.rubric_feedback.run` |
| **C — Human–AI Collaborative Grading** | TA review with `verify_temporal_oversight` | [`ta_review/run.py`](ta_review/run.py) | `python -m usecases.education.ta_review.run` |
| Audit (B follow-up) | SPARQL queries over the Scenario B envelope | [`sparql_queries.py`](sparql_queries.py) | `python -m usecases.education.sparql_queries` |
| Supplementary — multimodal variant | Classroom benchmark (500 × 6 sentences, audio) | [`oral_feedback/run.py`](oral_feedback/run.py) | `python -m usecases.education.oral_feedback.run` |

The static envelope sample referenced from documentation lives in
[`envelope_samples/`](envelope_samples/) — a copy-paste reference, not
runnable.

## Patterns each scenario exercises

- **Scenario A** — `verify_negative_proof`, `verify_workflow_isolation`,
  `verify_pii_detachment`. Uses `InMemoryPIIVault` to detach identity
  attributes so the grading chain can be proved structurally identity-blind.
- **Scenario B** — bundled-envelope pattern via `userml_payload(...)` with
  per-sentence Interpretation+Application pairs. The single-submission
  variant additionally runs `ForwardingEnforcer` to demonstrate
  Semantic-Forward at the handoff (downstream consumers see only
  `semantic_payload`, never raw artifact hashes / proof / provenance).
  The classroom-benchmark variant adds `verify_rubric_grounding`.
- **Scenario C** — temporal chain: AI grading activity → TA review activity
  (with per-document open timestamps) → grade commit. Audited via
  `verify_temporal_oversight` (configurable `min_review_seconds`).
- **Supplementary multimodal** — same as B but submissions are audio,
  per-sentence interpretations bind to `(start_ms, end_ms)` windows on the
  audio artifact, audited via `verify_multimodal_binding`.

## Outputs

All scenarios write to the repo-level `output/` directory. The relevant
artifacts per scenario:

| Scenario | Envelope(s) | PROV graph | Audit | Metrics |
|---|---|---|---|---|
| A (`run.py`) | `education_envelope.json` | `education_prov.ttl`, `education_equity_prov.ttl` | `education_audit.json` | `education_metrics.json` |
| B (`scenario_b.py`) | `education_scenario_b_envelope.json`, `education_scenario_b_forward.json` | `education_scenario_b_prov.ttl` | (none, demonstrative) | (printed) |
| B-classroom (`rubric_feedback/run.py`) | `rubric_feedback_envelopes.jsonl` | `rubric_feedback_prov.ttl` | `rubric_feedback_audit.json` | `rubric_feedback_metrics.json` |
| C (`ta_review/run.py`) | `ta_review_envelope.json` | `ta_review_prov.ttl` | `ta_review_audit.json` | `ta_review_metrics.json` |
| Supplementary multimodal (`oral_feedback/run.py`) | `oral_feedback_envelopes.jsonl` | `oral_feedback_prov.ttl` | `oral_feedback_audit.json` | `oral_feedback_metrics.json` |

## Common conventions

- All scenarios set `risk_level=HIGH`, which auto-selects
  `forwarding_policy=semantic_forward` on the envelope.
- Feature-suppression vocabulary (Scenario A, fixed):
  `["student_name", "student_id", "accommodation_flags", "prior_grades"]`.
- All envelopes are signed with an Ed25519 key from `did:university:*`
  identifiers; signatures are verified by `verify_integrity`.
