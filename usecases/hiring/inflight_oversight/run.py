"""In-flight Art. 14 oversight at the screening -> recruiter handoff.

Quadripartite forwarding: identifying tokens (name, photograph, DOB,
citizenship, gender, marital status, address) plus raw async-interview video
are suppressed at the boundary; only their derived semantic statements
(skills_overlap, tenure_pattern_score, language_signal_score) reach the
recruiter. The recruiter spends a meaningful interval per candidate so the
temporal-oversight verifier passes.

Verifiers exercised:
  - verify_negative_proof              (suppressed identifiers absent from chain)
  - verify_no_prohibited_practice      (Art. 5(1)(f)/(g))
  - verify_candidate_notice            (Art. 26(11) + Art. 50)
  - verify_temporal_oversight          (Art. 14)
  - verify_ai_literacy_attestation     (Arts. 4 / 14(4))
  - verify_integrity                   (envelope signature + content-hash)

Default fixtures: 28 shortlisted candidates, recruiter spends 4 minutes per
candidate (4 * 60 = 240 s; aggregate 28 * 240 = 6720 s). With
``--inject-violation`` the recruiter rubber-stamps the batch in 90 s
(below the 300 s minimum), so verify_temporal_oversight fails.

Outputs (usecases/output/):
  hiring_inflight_envelope.json
  hiring_inflight_prov.ttl
  hiring_inflight_audit.json
  hiring_inflight_metrics.json
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
    verify_temporal_oversight,
)
from jhcontext.pii import InMemoryPIIVault

from usecases.hiring import fixtures as fx
from usecases.hiring.verifiers import (
    verify_ai_literacy_attestation,
    verify_candidate_notice,
    verify_no_prohibited_practice,
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

    candidates = fx.shortlisted_candidates(n=28)
    competence = fx.recruiter_competence_record()
    ts = fx.default_attestation_timestamps()

    base = ts.deployment_anchor
    screening_start = base + timedelta(hours=2)
    screening_end   = screening_start + timedelta(minutes=15)
    recruiter_start = screening_end + timedelta(minutes=5)

    # Per-candidate review duration: 4 min default; 90 s/batch under violation
    if inject_violation:
        recruiter_total_seconds = 90.0
    else:
        recruiter_total_seconds = 4 * 60 * len(candidates)
    recruiter_end = recruiter_start + timedelta(seconds=recruiter_total_seconds)

    # =====================================================================
    # PROV graph (single graph; the recruiter is the human overseer)
    # =====================================================================
    prov = PROVGraph("ctx-hiring-inflight")
    prov.add_agent("screening-agent", "Screening Agent", role="ranker")
    prov.add_agent("recruiter-jane", "Jane Doe", role="recruiter")
    prov.set_entity_attribute(
        "recruiter-jane", "competenceRecordHash", competence.competence_record_hash,
    )
    prov.set_entity_attribute(
        "recruiter-jane", "competenceRecordSigner", competence.competence_record_signer,
    )

    # Source artifacts: raw video + raw CV (suppressed; recorded as types
    # the negative-proof check excludes from the screening output's chain).
    prov.add_entity("art-raw-video", "Async-interview raw video",
                    artifact_type="raw_video")
    prov.add_entity("art-raw-cv", "Raw candidate CV with identifiers",
                    artifact_type="raw_cv_with_identifiers")
    # Semantic statements derived from the raw inputs (Quadripartite boundary).
    prov.add_entity("art-screening-rank", "Screening rank list (semantic only)",
                    artifact_type="semantic_extraction")

    prov.add_activity("screening", "Screening handoff",
                      started_at=_iso(screening_start),
                      ended_at=_iso(screening_end),
                      method="screener-v1.4 + transcript embedding")
    prov.was_associated_with("screening", "screening-agent")
    # Note: screening DOES NOT 'use' raw video/CV directly in PROV -- they
    # were already converted to semantic_extraction upstream. This is the
    # whole point of Quadripartite/Semantic-Forward at the boundary.
    prov.was_generated_by("art-screening-rank", "screening")

    prov.add_activity("recruiter-review",
                      "Recruiter reviews shortlist (Art. 14 oversight)",
                      started_at=_iso(recruiter_start),
                      ended_at=_iso(recruiter_end),
                      method="manual review of semantic statements")
    prov.was_associated_with("recruiter-review", "recruiter-jane")
    prov.used("recruiter-review", "art-screening-rank")

    # =====================================================================
    # Envelope at the screening -> recruiter handoff
    # =====================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()

    semantic_payload = userml_payload(
        observations=[
            observation(c.candidate_id, "skills_overlap", round(c.skills_overlap, 3))
            for c in candidates
        ],
        interpretations=[
            interpretation(c.candidate_id, "tenure_pattern_score",
                           round(c.tenure_pattern_score, 3))
            for c in candidates
        ] + [
            interpretation(c.candidate_id, "language_signal_score",
                           round(c.language_signal_score, 3))
            for c in candidates
        ],
    )

    builder = (
        EnvelopeBuilder()
        .set_producer(fx.PRODUCERS["screening"])
        .set_scope("hiring_inflight_screening_to_recruiter")
        .set_ttl("PT4H")
        .set_risk_level(RiskLevel.HIGH)  # auto: SEMANTIC_FORWARD
        .set_human_oversight(True)
        .set_semantic_payload([semantic_payload])
        .add_decision_influence(
            agent="screening-agent",
            categories=list(fx.SCREENING_WEIGHTS.keys()),
            influence_weights=dict(fx.SCREENING_WEIGHTS),
            confidence=0.88,
            abstraction_level=AbstractionLevel.SITUATION,
            temporal_scope=TemporalScope.CURRENT,
        )
    )

    # Model artifact (screener) with data-governance attestation.
    models = fx.vendor_models()
    builder.add_artifact(
        artifact_id=models[0].artifact_id,
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash=models[0].content_hash(),
        model=models[0].model,
        capabilities=list(models[0].capabilities),
        data_governance_attestation_ref=models[0].data_governance_attestation_ref,
        data_governance_attestation_signer=models[0].data_governance_attestation_signer,
    )

    # One candidate-notice attestation per shortlisted candidate.
    notice_dt = recruiter_start - ts.candidate_notice_offset
    for c in candidates:
        builder.add_artifact(
            artifact_id=f"att-cand-notice-{c.candidate_id}",
            artifact_type=ArtifactType.TOOL_RESULT,
            content_hash="sha256:" + ("c" * 64),
            kind="candidate_notice_attestation",
            candidate_id=c.candidate_id,
            signer="did:deployer:notification-service",
            attestation_hash=f"sha256:notice-{c.candidate_id}",
            attestation_timestamp=_iso(notice_dt),
        )

    # Decision artifact (the rank list itself).
    builder.add_artifact(
        artifact_id="art-decision",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:" + "d" * 64,
        kind="decision",
    )
    builder._envelope.artifacts_registry[-1].timestamp = _iso(recruiter_end)
    builder.set_passed_artifact("art-decision")

    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://vendor.example/models/screener-v1.4",
        escalation_path="recruiting-lead@deployer.example",
    )
    builder.set_privacy(
        data_category="sensitive",
        legal_basis="consent",
        retention="P30D",
        feature_suppression=list(fx.SUPPRESSED_IDENTIFIERS),
    )
    builder.enable_pii_detachment(vault=pii_vault)

    prov_digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest

    builder.sign(fx.COMPLIANCE_SIGNER)
    envelope = builder.build()
    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # Audit
    # =====================================================================
    t0 = time.perf_counter()

    excluded_types = ["raw_video", "raw_cv_with_identifiers"]
    neg_proof = verify_negative_proof(
        prov,
        decision_entity_id="art-screening-rank",
        excluded_artifact_types=excluded_types,
    )
    no_prohibited = verify_no_prohibited_practice(envelope)
    cand_notice = verify_candidate_notice(envelope, candidate_id=candidates[0].candidate_id)
    temporal = verify_temporal_oversight(
        prov,
        ai_activity_id="screening",
        human_activities=["recruiter-review"],
        min_review_seconds=300.0,
    )
    ai_literacy = verify_ai_literacy_attestation(prov, oversight_activity_id="recruiter-review")
    integ = verify_integrity(envelope)

    results = [neg_proof, no_prohibited, cand_notice, temporal, ai_literacy, integ]
    report = generate_audit_report(envelope, prov, results)
    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_total) * 1000

    # =====================================================================
    # Persist outputs
    # =====================================================================
    (OUTPUT_DIR / "hiring_inflight_envelope.json").write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "hiring_inflight_prov.ttl").write_text(
        prov.serialize("turtle"), encoding="utf-8",
    )
    (OUTPUT_DIR / "hiring_inflight_audit.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics["prov_entities"] = len(prov.get_all_entities())
    metrics["prov_activities"] = len(prov.get_temporal_sequence())
    metrics["candidates"] = len(candidates)
    metrics["recruiter_total_seconds"] = recruiter_total_seconds
    metrics["overall_passed"] = report.overall_passed
    metrics["decision_influence_weights"] = dict(fx.SCREENING_WEIGHTS)

    (OUTPUT_DIR / "hiring_inflight_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8",
    )

    # =====================================================================
    # Console summary
    # =====================================================================
    print("=" * 64)
    print("Hiring Scenario B -- In-flight Art. 14 Oversight")
    print("=" * 64)
    print(f"  Inject violation: {inject_violation}")
    print(f"  Forwarding:       {envelope.compliance.forwarding_policy.value}")
    print(f"  Suppressed:       {', '.join(fx.SUPPRESSED_IDENTIFIERS)}")
    print(f"  Candidates:       {len(candidates)}")
    print(f"  Recruiter time:   {recruiter_total_seconds:.0f} s")
    print(f"  PROV entities:    {metrics['prov_entities']}")
    print(f"  PROV activities:  {metrics['prov_activities']}")
    print()
    print("  Audit (Arts. 4 / 5(1)(f)(g) / 14 / 26(11) / 50 + integrity):")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall:          {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Total runtime:    {metrics['total_ms']:.1f} ms")
    print(f"  Outputs:          {OUTPUT_DIR}/hiring_inflight_*")
    print("=" * 64)
    return metrics


if __name__ == "__main__":
    run(inject_violation=_inject_flag(sys.argv[1:]))
