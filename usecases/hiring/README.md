# Hiring / Employment Scenarios

Three end-to-end demonstrations of the PAC-AI protocol applied to a multi-agent
hiring pipeline (sourcing → parsing → screening → async-interview → ranking →
decision-support). Each scenario stresses a different governance handoff and
exercises a distinct subset of verifiers.

## Scenario map

| Scenario | Handoff stressed | Verifiers fired | Entry point |
|---|---|---|---|
| **A — Procurement** | sourcing/parsing → screening | sourcing_neutrality · negative_proof · no_prohibited_practice · workforce_notice · ai_literacy · input_data · integrity | `python -m usecases.hiring.procurement.run` |
| **B — In-flight oversight** | screening → recruiter (Quadripartite forwarding) | negative_proof · no_prohibited_practice · candidate_notice · temporal_oversight · ai_literacy · integrity | `python -m usecases.hiring.inflight_oversight.run` |
| **C — Cohort audit** | deployer → regulator | negative_proof · temporal_oversight (per-receipt) · feature_usage_census · four_fifths_ratio · incident_attestation (corpus) | `python -m usecases.hiring.cohort_audit.run` |
| All three | — | — | `python -m usecases.hiring.run_all` |

Pass `--inject-violation` (or set `HIRING_INJECT_VIOLATION=1`) to seed the
specific violation each scenario is built to detect.

## Layout

```
usecases/hiring/
  verifiers.py             # 7 HR-specific verifiers (local helpers)
  cohort.py                # feature_usage_census + four_fifths_ratio
  fixtures.py              # synthetic candidates, ad params, suspensions, attestations
  procurement/run.py       # Scenario A
  inflight_oversight/run.py# Scenario B
  cohort_audit/run.py      # Scenario C
  run_all.py
```

## Verifier glossary

The four domain-portable verifiers (`verify_negative_proof`,
`verify_temporal_oversight`, `verify_workflow_isolation`,
`verify_pii_detachment`) ship in the SDK at `jhcontext.audit`. The seven
HR-specific verifiers below live in `usecases/hiring/verifiers.py`:

| Verifier | Inspects | Pass condition |
|---|---|---|
| `verify_no_prohibited_practice` | `envelope.artifacts_registry[*].metadata.capabilities` | None of the declared capabilities is in the workplace-banned set (defaults: `workplace_emotion_inference`, `protected_attribute_biometric_categorisation`) |
| `verify_sourcing_neutrality` | PROV graph; entities reachable from the sourcing decision | None of the `adTargetingParam` attributes is in the prohibited set |
| `verify_workforce_notice` | `artifacts_registry` for a `kind=workforce_notice_attestation` entry | Has signer + attestation_hash + attestation_timestamp; timestamp predates `envelope.created_at` |
| `verify_candidate_notice` | `artifacts_registry` for a `kind=candidate_notice_attestation` matching `candidate_id` | Has signer + timestamp; timestamp predates the decision artifact's timestamp |
| `verify_ai_literacy_attestation` | PROV agent associated with the named oversight activity | Agent carries `competenceRecordHash` + `competenceRecordSigner` |
| `verify_input_data_attestation` | every model-bearing artifact in the registry | Each has `data_governance_attestation_ref` + `data_governance_attestation_signer` |
| `verify_incident_attestation` | PROV graph; activities tagged `kind=suspension` | Each suspension has a downstream `kind=art73_notification` activity within 15 calendar days |

## Outputs

All scenarios write to `usecases/output/`:

- Scenario A — `hiring_procurement_envelope_{sourcing,parsing,screening}.json`,
  `hiring_procurement_prov.ttl`, `hiring_procurement_audit.json`,
  `hiring_procurement_metrics.json`
- Scenario B — `hiring_inflight_envelope.json`, `hiring_inflight_prov.ttl`,
  `hiring_inflight_audit.json`, `hiring_inflight_metrics.json`
- Scenario C — `hiring_cohort_corpus.jsonl` (one envelope per line),
  `hiring_cohort_audit.json`, `hiring_cohort_census.json`,
  `hiring_cohort_four_fifths.json`, `hiring_cohort_incidents.json`,
  `hiring_cohort_metrics.json`

## Tests

Pass/fail unit coverage for the seven verifiers and the two cohort helpers:

```
uv run --with pytest pytest tests/test_hiring_verifiers.py tests/test_hiring_cohort.py -v
```
