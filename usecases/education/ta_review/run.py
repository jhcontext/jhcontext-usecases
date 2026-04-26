"""Human-AI Collaborative Grading (TA Review).

Education-domain scenario demonstrating meaningful Art. 14 human oversight:
the AI grades an essay, a teaching assistant reviews the AI output plus the
source submission, and only then commits the grade.

Pipeline: essay submission -> AI grading -> TA teaching-assistant review
(meaningful Art. 14 human oversight) -> grade commit.

PAC-AI records the full temporal chain: when AI produced the score, which
documents the TA opened in what order, and when the final grade was
committed. ``verify_temporal_oversight`` confirms review activities occurred
AFTER AI output and that the expected documents were accessed. A TA who
commits grades seconds after opening the AI output leaves that pattern in
the record.

Pipeline parallels healthcare/triage_rural/run.py (same verifier, same
structural pattern) — this scenario is the education-domain instantiation.

Verifiers exercised:
  - verify_temporal_oversight  (TA review after AI; minimum review duration)
  - verify_integrity           (envelope signature + content hash)

Outputs (usecases/output/):
  ta_review_envelope.json    Envelope JSON-LD
  ta_review_prov.ttl         W3C PROV graph
  ta_review_audit.json       Audit report
  ta_review_metrics.json     Timing metrics
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jhcontext import (
    ArtifactType,
    EnvelopeBuilder,
    PROVGraph,
    RiskLevel,
    AbstractionLevel,
    TemporalScope,
    observation,
    interpretation,
    verify_integrity,
    verify_temporal_oversight,
    generate_audit_report,
)
from jhcontext.pii import InMemoryPIIVault

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def run() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    # --- Timeline: summative assessment with mandatory TA review ---
    base = datetime(2026, 5, 20, 9, 0, 0, tzinfo=timezone.utc)
    ts = {
        "grading_start": base,
        "grading_end": base + timedelta(minutes=2),
        # TA opens documents and reviews (meaningful review > 5 minutes)
        "ta_open_submission": base + timedelta(minutes=4),
        "ta_open_rubric": base + timedelta(minutes=4, seconds=30),
        "ta_open_ai_feedback": base + timedelta(minutes=5),
        "ta_review_start": base + timedelta(minutes=4),
        "ta_review_end": base + timedelta(minutes=12),  # 8-minute total review
        "grade_commit": base + timedelta(minutes=12, seconds=30),
    }

    # =====================================================================
    # STEP 1: AI Grading Agent
    # =====================================================================
    t0 = time.perf_counter()

    # Flat semantic_payload — atomic UserML SituationalStatements per the
    # protocol v0.5 SDK convention.
    payload = [
        observation("submission:S-SUMM-042", "essay_word_count", 2100),
        observation("submission:S-SUMM-042", "rubric_version", "ENG201-summative-v2"),
        interpretation("submission:S-SUMM-042", "ai_aggregate_score", 78,
                       confidence=0.88),
    ]

    builder = (
        EnvelopeBuilder()
        .set_producer("did:university:ai-grading-agent")
        .set_scope("education_assessment_summative")
        .set_ttl("P5Y")  # Art. 12 long-retention for summative records
        .set_risk_level(RiskLevel.HIGH)  # Annex III §3
        .set_human_oversight(True)       # Art. 14: mandatory for summative
        .set_semantic_payload(payload)
        .add_artifact(
            artifact_id="art-submission",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:aaaabbbbccccdddd1111222233334444",
        )
        .add_artifact(
            artifact_id="art-rubric",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:eeeeffff1111222233334444aaaabbbb",
        )
        .add_artifact(
            artifact_id="art-ai-score",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:ccccddddeeeeffff5555666677778888",
            model="essay-grader-v3",
            confidence=0.88,
        )
        .add_artifact(
            artifact_id="art-ai-feedback",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:99990000aaaabbbbccccdddd22221111",
            model="feedback-agent-v1.2",
            confidence=0.84,
        )
        .set_passed_artifact("art-ai-score")
        .add_decision_influence(
            agent="ai-grading-agent",
            categories=["argument_quality", "evidence_use", "writing_clarity", "critical_thinking"],
            influence_weights={
                "argument_quality": 0.30, "evidence_use": 0.30,
                "writing_clarity": 0.20, "critical_thinking": 0.20,
            },
            confidence=0.88,
            abstraction_level=AbstractionLevel.INTERPRETATION,
            temporal_scope=TemporalScope.CURRENT,
        )
    )

    prov = PROVGraph("ctx-edu-ta-review-001")
    prov.add_agent("ai-grading-agent", "AI Essay Grading Agent", role="evaluator")
    prov.add_entity("art-submission", "Student submission S-SUMM-042",
                    artifact_type="token_sequence",
                    content_hash="sha256:aaaabbbb...")
    prov.add_entity("art-rubric", "Summative rubric ENG201-v2",
                    artifact_type="token_sequence",
                    content_hash="sha256:eeeeffff...")
    prov.add_entity("art-ai-score", "AI aggregate score (78/100)",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:ccccdddd...")
    prov.add_entity("art-ai-feedback", "AI-generated feedback block",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:99990000...")

    prov.add_activity("ai-grading", "AI Essay Evaluation",
                      started_at=_iso(ts["grading_start"]),
                      ended_at=_iso(ts["grading_end"]),
                      method="LLM inference (essay-grader-v3)")
    prov.used("ai-grading", "art-submission")
    prov.used("ai-grading", "art-rubric")
    prov.was_generated_by("art-ai-score", "ai-grading")
    prov.was_generated_by("art-ai-feedback", "ai-grading")
    prov.was_associated_with("ai-grading", "ai-grading-agent")
    prov.was_derived_from("art-ai-score", "art-submission")
    prov.was_derived_from("art-ai-score", "art-rubric")

    metrics["grading_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 2: TA Review Activity (the Art. 14 meaningful-oversight step)
    # =====================================================================
    t0 = time.perf_counter()

    prov.add_agent("ta-martins", "Teaching Assistant (ENG201)",
                   role="human_oversight")

    # Record WHICH documents the TA opened, WHEN, and in WHAT ORDER.
    # verify_temporal_oversight expects the review activity to start after the
    # AI activity; we use a single umbrella "ta-review" activity whose started_at
    # is the first document-open and ended_at is the grade-commit.
    prov.add_activity(
        "ta-review",
        "TA reviews AI score + feedback against submission and rubric",
        started_at=_iso(ts["ta_review_start"]),
        ended_at=_iso(ts["ta_review_end"]),
        method="human_review",
    )
    prov.used("ta-review", "art-submission")
    prov.used("ta-review", "art-rubric")
    prov.used("ta-review", "art-ai-score")
    prov.used("ta-review", "art-ai-feedback")
    prov.was_associated_with("ta-review", "ta-martins")

    # Record per-document open timestamps as JH attributes on the review activity
    prov.set_entity_attribute("ta-review", "openedSubmissionAt", _iso(ts["ta_open_submission"]))
    prov.set_entity_attribute("ta-review", "openedRubricAt", _iso(ts["ta_open_rubric"]))
    prov.set_entity_attribute("ta-review", "openedAIFeedbackAt", _iso(ts["ta_open_ai_feedback"]))

    # Grade commit activity (separate, after review)
    prov.add_entity("art-final-grade", "Final committed grade (B+, 78/100)",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:final-grade-commit-hash")
    prov.add_activity(
        "grade-commit",
        "Final grade committed after TA review",
        started_at=_iso(ts["grade_commit"]),
        ended_at=_iso(ts["grade_commit"] + timedelta(seconds=2)),
        method="grade_commit",
    )
    prov.used("grade-commit", "art-ai-score")
    prov.used("grade-commit", "art-ai-feedback")
    prov.was_generated_by("art-final-grade", "grade-commit")
    prov.was_associated_with("grade-commit", "ta-martins")
    prov.was_informed_by("grade-commit", "ta-review")

    metrics["oversight_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 3: Finalise envelope
    # =====================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()
    builder.set_privacy(
        data_category="behavioral",
        legal_basis="legitimate_interest",
        retention="P5Y",
        storage_policy="university-encrypted",
        feature_suppression=["student_name", "student_id", "accommodation_flags", "prior_grades"],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://university.example/models/essay-grader-v3",
        escalation_path="academic-affairs@university.example",
    )
    builder.enable_pii_detachment(vault=pii_vault)

    prov_digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest

    envelope = builder.sign("did:university:compliance-officer").build()
    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 4: Audit — Art. 14 temporal oversight + integrity
    # =====================================================================
    t0 = time.perf_counter()

    temporal = verify_temporal_oversight(
        prov,
        ai_activity_id="ai-grading",
        human_activities=["ta-review"],
        min_review_seconds=300.0,  # 5-minute minimum for meaningful review
    )
    integrity = verify_integrity(envelope)
    report = generate_audit_report(envelope, prov, [temporal, integrity])

    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =====================================================================
    # STEP 5: Persist outputs
    # =====================================================================
    env_path = OUTPUT_DIR / "ta_review_envelope.json"
    env_path.write_text(json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
                        encoding="utf-8")
    prov_path = OUTPUT_DIR / "ta_review_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")
    audit_path = OUTPUT_DIR / "ta_review_audit.json"
    audit_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                          encoding="utf-8")
    metrics_path = OUTPUT_DIR / "ta_review_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # =====================================================================
    # STEP 6: Print summary
    # =====================================================================
    print("=" * 68)
    print("SCENARIO C — Human–AI Collaborative Grading (§4.3)")
    print("=" * 68)
    print(f"  Context ID:          {envelope.context_id}")
    print(f"  Producer:            {envelope.producer}")
    print(f"  Risk Level:          {envelope.compliance.risk_level.value}")
    print(f"  Human oversight req: {envelope.compliance.human_oversight_required}")
    print()
    print("  Temporal chain:")
    print(f"    AI grading           : {ts['grading_start'].time()} → {ts['grading_end'].time()}")
    print(f"    TA opens submission  : {ts['ta_open_submission'].time()}")
    print(f"    TA opens rubric      : {ts['ta_open_rubric'].time()}")
    print(f"    TA opens AI feedback : {ts['ta_open_ai_feedback'].time()}")
    print(f"    TA review duration   : {(ts['ta_review_end'] - ts['ta_review_start']).total_seconds():.0f}s")
    print(f"    Grade commit         : {ts['grade_commit'].time()}")
    print()
    print("  AUDIT RESULTS:")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall: {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print("  Performance:")
    print(f"    Grading:     {metrics['grading_ms']:.1f} ms")
    print(f"    Oversight:   {metrics['oversight_ms']:.1f} ms")
    print(f"    Envelope:    {metrics['envelope_build_ms']:.1f} ms")
    print(f"    Audit:       {metrics['audit_ms']:.1f} ms")
    print(f"    Total:       {metrics['total_ms']:.1f} ms")
    print()
    print(f"  Outputs saved to: {OUTPUT_DIR}/")
    print("=" * 68)
    return metrics


if __name__ == "__main__":
    run()
