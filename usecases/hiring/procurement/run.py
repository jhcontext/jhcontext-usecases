"""Procurement-time governance.

Stresses the sourcing/parsing -> screening handoff. The deployer is selecting
a vendor pipeline; verifiers fire at procurement time so violations surface
before any candidate data flows. Three envelopes are emitted (one per stage)
and audited together.

Verifiers exercised:
  - verify_negative_proof              (suppressed identifiers absent from chain)
  - verify_no_prohibited_practice      (Art. 5(1)(f)/(g))
  - verify_sourcing_neutrality         (Annex III §4(a))
  - verify_workforce_notice            (Art. 26(7))
  - verify_ai_literacy_attestation     (Arts. 4 / 14(4))
  - verify_input_data_attestation      (Art. 26(4))
  - verify_integrity                   (envelope signature + content-hash)

Inject a violation by setting environment variable HIRING_INJECT_VIOLATION=1
or passing ``--inject-violation``: the interview model declares
``workplace_emotion_inference`` and a banned ad-targeting parameter is added.

Outputs (usecases/output/):
  hiring_procurement_envelope_<stage>.json
  hiring_procurement_prov.ttl
  hiring_procurement_audit.json
  hiring_procurement_metrics.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jhcontext import (
    AbstractionLevel,
    ArtifactType,
    EnvelopeBuilder,
    PROVGraph,
    RiskLevel,
    TemporalScope,
    generate_audit_report,
    interpretation,
    observation,
    userml_payload,
    verify_integrity,
    verify_negative_proof,
)

from usecases.hiring import fixtures as fx
from usecases.hiring.verifiers import (
    verify_ai_literacy_attestation,
    verify_input_data_attestation,
    verify_no_prohibited_practice,
    verify_sourcing_neutrality,
    verify_workforce_notice,
)

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _inject_flag(argv: list[str]) -> bool:
    if "--inject-violation" in argv:
        return True
    return os.environ.get("HIRING_INJECT_VIOLATION", "").lower() in {"1", "true", "yes"}


def run(*, inject_violation: bool = False) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {"inject_violation": inject_violation}
    t_total = time.perf_counter()

    ts = fx.default_attestation_timestamps()
    base = ts.deployment_anchor
    timeline = {
        "sourcing_start":  base,
        "sourcing_end":    base + timedelta(minutes=2),
        "parsing_start":   base + timedelta(minutes=3),
        "parsing_end":     base + timedelta(minutes=4),
        "screening_start": base + timedelta(minutes=5),
        "screening_end":   base + timedelta(minutes=8),
        "procurement_review_start": base + timedelta(minutes=10),
        "procurement_review_end":   base + timedelta(minutes=40),  # 30-min review
    }

    candidates = fx.synthetic_candidates(n=5)
    models = fx.vendor_models(with_violation=inject_violation)
    targeting = fx.sourcing_targeting_params(with_violation=inject_violation)
    competence = fx.recruiter_competence_record()

    # =====================================================================
    # PROV graph (single graph spans all three stages)
    # =====================================================================
    prov = PROVGraph("ctx-hiring-procurement")
    prov.add_agent("sourcing-agent", "Sourcing Agent", role="sourcing")
    prov.add_agent("parsing-agent", "Parsing Agent", role="parser")
    prov.add_agent("screening-agent", "Screening Agent", role="ranker")
    prov.add_agent("recruiter-jane", "Jane Doe", role="recruiter")
    # The recruiter as overseer carries the AI-literacy competence record.
    prov.set_entity_attribute(
        "recruiter-jane", "competenceRecordHash", competence.competence_record_hash,
    )
    prov.set_entity_attribute(
        "recruiter-jane", "competenceRecordSigner", competence.competence_record_signer,
    )

    # ---- Sourcing stage ---------------------------------------------------
    prov.add_entity("art-ad-config", "Sourcing ad config", artifact_type="config")
    for p in targeting:
        prov.set_entity_attribute("art-ad-config", "adTargetingParam", p)
    prov.add_entity("art-sourcing-decision", "Candidate-pool selection",
                    artifact_type="semantic_extraction")
    prov.add_activity("sourcing", "Sourcing handoff",
                      started_at=_iso(timeline["sourcing_start"]),
                      ended_at=_iso(timeline["sourcing_end"]),
                      method="ad-platform query")
    prov.was_associated_with("sourcing", "sourcing-agent")
    prov.used("sourcing", "art-ad-config")
    prov.was_generated_by("art-sourcing-decision", "sourcing")
    prov.was_derived_from("art-sourcing-decision", "art-ad-config")

    # ---- Parsing stage ----------------------------------------------------
    prov.add_entity("art-parsed-cohort", "Parsed candidate records",
                    artifact_type="semantic_extraction")
    prov.add_activity("parsing", "Parsing handoff",
                      started_at=_iso(timeline["parsing_start"]),
                      ended_at=_iso(timeline["parsing_end"]),
                      method="cv-parser-v3.1")
    prov.was_associated_with("parsing", "parsing-agent")
    prov.used("parsing", "art-sourcing-decision")
    prov.was_generated_by("art-parsed-cohort", "parsing")
    prov.was_derived_from("art-parsed-cohort", "art-sourcing-decision")

    # ---- Screening stage --------------------------------------------------
    prov.add_entity("art-screening-rank", "Screening rank list",
                    artifact_type="semantic_extraction")
    prov.add_activity("screening", "Screening handoff",
                      started_at=_iso(timeline["screening_start"]),
                      ended_at=_iso(timeline["screening_end"]),
                      method="screener-v1.4")
    prov.was_associated_with("screening", "screening-agent")
    prov.used("screening", "art-parsed-cohort")
    prov.was_generated_by("art-screening-rank", "screening")
    prov.was_derived_from("art-screening-rank", "art-parsed-cohort")

    # ---- Procurement review (HR director + DPO + compliance) -------------
    # The deployer's overseer reviews the proposed pipeline at procurement
    # time. The competence record on this overseer is what verify_ai_literacy
    # binds to.
    prov.add_activity("procurement-review",
                      "HR director + DPO procurement review",
                      started_at=_iso(timeline["procurement_review_start"]),
                      ended_at=_iso(timeline["procurement_review_end"]),
                      method="manual review of pipeline disclosures")
    prov.was_associated_with("procurement-review", "recruiter-jane")
    prov.used("procurement-review", "art-screening-rank")

    # =====================================================================
    # Three envelopes (one per handoff). Sourcing carries the workforce
    # notice + ad-targeting attestation; parsing/screening carry
    # data-governance attestations on their model artifacts.
    # =====================================================================
    envelopes: dict[str, dict] = {}

    # ---- Helper to add the four "always present" attestations / artifacts -
    def _attach_common_attestations(b: EnvelopeBuilder, *, model: fx.VendorModel) -> None:
        # Workforce notice attestation (collective notice 30 days pre-deployment).
        b.add_artifact(
            artifact_id="att-workforce",
            artifact_type=ArtifactType.TOOL_RESULT,
            content_hash="sha256:wf-" + ts.workforce_notice.isoformat()[:10].replace("-", ""),
            kind="workforce_notice_attestation",
            signer=fx.DEPLOYER_SIGNER,
            attestation_hash="sha256:wf-2026Q1-collective-notice",
            attestation_timestamp=_iso(ts.workforce_notice),
        )
        # Model artifact for the stage (carries data-governance attestation).
        b.add_artifact(
            artifact_id=model.artifact_id,
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash=model.content_hash(),
            model=model.model,
            capabilities=list(model.capabilities),
            data_governance_attestation_ref=model.data_governance_attestation_ref,
            data_governance_attestation_signer=model.data_governance_attestation_signer,
        )

    # ---- 1. Sourcing envelope --------------------------------------------
    t0 = time.perf_counter()
    sourcing_payload = userml_payload(
        observations=[
            observation("policy:targeting", "ad_param", p) for p in targeting
        ],
        interpretations=[
            interpretation("policy:targeting", "neutrality_review",
                           "passed" if not inject_violation else "review-required"),
        ],
    )
    sourcing_b = (
        EnvelopeBuilder()
        .set_producer(fx.PRODUCERS["sourcing"])
        .set_scope("hiring_procurement_sourcing")
        .set_ttl("PT1H")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([sourcing_payload])
    )
    _attach_common_attestations(sourcing_b, model=models[0])  # screener model
    sourcing_b.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref=f"https://vendor.example/models/{models[0].model}",
        escalation_path="hr-director@deployer.example",
    )
    sourcing_b.set_privacy(
        data_category="behavioral",
        legal_basis="legitimate_interest",
        retention="P30D",
        feature_suppression=list(fx.SUPPRESSED_IDENTIFIERS),
    )
    sourcing_b.sign(fx.COMPLIANCE_SIGNER)
    env_sourcing = sourcing_b.build()
    envelopes["sourcing"] = env_sourcing.to_jsonld()
    metrics["sourcing_ms"] = (time.perf_counter() - t0) * 1000

    # ---- 2. Parsing envelope ---------------------------------------------
    t0 = time.perf_counter()
    parsing_payload = userml_payload(
        observations=[observation(c.candidate_id, "experience_band", c.experience_band)
                      for c in candidates],
        interpretations=[interpretation(c.candidate_id, "skills_overlap",
                                        round(c.skills_overlap, 3))
                         for c in candidates],
    )
    parsing_b = (
        EnvelopeBuilder()
        .set_producer(fx.PRODUCERS["parsing"])
        .set_scope("hiring_procurement_parsing")
        .set_ttl("PT1H")
        .set_risk_level(RiskLevel.HIGH)
        .set_semantic_payload([parsing_payload])
    )
    _attach_common_attestations(parsing_b, model=models[1])  # parser model
    parsing_b.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref=f"https://vendor.example/models/{models[1].model}",
        escalation_path="hr-director@deployer.example",
    )
    parsing_b.set_privacy(
        data_category="sensitive",
        legal_basis="consent",
        retention="P7D",
        feature_suppression=list(fx.SUPPRESSED_IDENTIFIERS),
    )
    parsing_b.sign(fx.COMPLIANCE_SIGNER)
    env_parsing = parsing_b.build()
    envelopes["parsing"] = env_parsing.to_jsonld()
    metrics["parsing_ms"] = (time.perf_counter() - t0) * 1000

    # ---- 3. Screening envelope -------------------------------------------
    t0 = time.perf_counter()
    screening_payload = userml_payload(
        observations=[observation(c.candidate_id, "skills_overlap",
                                  round(c.skills_overlap, 3))
                      for c in candidates],
        interpretations=[interpretation(c.candidate_id, "rank_score",
                                        round(c.skills_overlap * 0.6 + c.tenure_pattern_score * 0.4, 3))
                         for c in candidates],
    )
    screening_b = (
        EnvelopeBuilder()
        .set_producer(fx.PRODUCERS["screening"])
        .set_scope("hiring_procurement_screening")
        .set_ttl("PT2H")
        .set_risk_level(RiskLevel.HIGH)
        .set_semantic_payload([screening_payload])
        .add_decision_influence(
            agent="screening-agent",
            categories=list(fx.SCREENING_WEIGHTS.keys()),
            influence_weights=dict(fx.SCREENING_WEIGHTS),
            confidence=0.88,
            abstraction_level=AbstractionLevel.SITUATION,
            temporal_scope=TemporalScope.CURRENT,
        )
    )
    _attach_common_attestations(screening_b, model=models[2])  # interview model
    # Add decision artifact so the screening envelope claims a decision.
    screening_b.add_artifact(
        artifact_id="art-decision",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:" + "d" * 64,
        kind="decision",
    )
    screening_b._envelope.artifacts_registry[-1].timestamp = _iso(timeline["screening_end"])
    screening_b.set_passed_artifact("art-decision")
    screening_b.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref=f"https://vendor.example/models/{models[2].model}",
        escalation_path="hr-director@deployer.example",
    )
    screening_b.set_privacy(
        data_category="sensitive",
        legal_basis="consent",
        retention="P30D",
        feature_suppression=list(fx.SUPPRESSED_IDENTIFIERS),
    )
    prov_digest = prov.digest()
    screening_b._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    screening_b._envelope.provenance_ref.prov_digest = prov_digest
    screening_b.sign(fx.COMPLIANCE_SIGNER)
    env_screening = screening_b.build()
    envelopes["screening"] = env_screening.to_jsonld()
    metrics["screening_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # Audit (apply verifiers across the three envelopes)
    # =====================================================================
    t0 = time.perf_counter()

    excluded_types = ["biometric_record", "geolocation_record"]
    sourcing_neut = verify_sourcing_neutrality(
        prov,
        sourcing_decision_entity_id="art-sourcing-decision",
        prohibited_targeting_attrs=list(fx.PROHIBITED_TARGETING_ATTRS),
    )
    neg_proof = verify_negative_proof(
        prov,
        decision_entity_id="art-screening-rank",
        excluded_artifact_types=excluded_types,
    )
    no_prohibited = verify_no_prohibited_practice(env_screening)
    workforce = verify_workforce_notice(env_screening)
    ai_literacy = verify_ai_literacy_attestation(prov, oversight_activity_id="procurement-review")
    input_data = verify_input_data_attestation(env_screening)
    integ = verify_integrity(env_screening)

    results = [sourcing_neut, neg_proof, no_prohibited, workforce, ai_literacy,
               input_data, integ]
    report = generate_audit_report(env_screening, prov, results)
    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_total) * 1000

    # =====================================================================
    # Persist outputs
    # =====================================================================
    for name, payload in envelopes.items():
        path = OUTPUT_DIR / f"hiring_procurement_envelope_{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    (OUTPUT_DIR / "hiring_procurement_prov.ttl").write_text(
        prov.serialize("turtle"), encoding="utf-8",
    )
    (OUTPUT_DIR / "hiring_procurement_audit.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )

    metrics["prov_entities"] = len(prov.get_all_entities())
    metrics["prov_activities"] = len(prov.get_temporal_sequence())
    metrics["envelopes"] = list(envelopes.keys())
    metrics["overall_passed"] = report.overall_passed

    (OUTPUT_DIR / "hiring_procurement_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8",
    )

    # =====================================================================
    # Console summary
    # =====================================================================
    print("=" * 64)
    print("Hiring Scenario A -- Procurement-time Governance")
    print("=" * 64)
    print(f"  Inject violation: {inject_violation}")
    print(f"  Envelopes:        {', '.join(envelopes)}")
    print(f"  PROV entities:    {metrics['prov_entities']}")
    print(f"  PROV activities:  {metrics['prov_activities']}")
    print()
    print("  Audit (Arts. 4 / 5(1)(f)(g) / 14 / 26 + Annex III §4(a)):")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall:          {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Total runtime:    {metrics['total_ms']:.1f} ms")
    print(f"  Outputs:          {OUTPUT_DIR}/hiring_procurement_*")
    print("=" * 64)
    return metrics


if __name__ == "__main__":
    run(inject_violation=_inject_flag(sys.argv[1:]))
