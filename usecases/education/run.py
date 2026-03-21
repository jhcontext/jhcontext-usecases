"""Education Scenario: Proving Fair Assessment (EU AI Act Article 13).

A university uses AI to grade student essays. This scenario demonstrates
how PAC-AI proves that student identity data (name, gender, disability
status) was NOT used in the grading decision — negative proof through
explicit enumeration.

Pipeline: Essay Ingestion → AI Grading → (Separate) Equity Reporting → Audit

Outputs:
  - output/education_envelope.json  (JSON-LD envelope)
  - output/education_prov.ttl       (W3C PROV graph — grading workflow)
  - output/education_equity_prov.ttl (W3C PROV graph — equity workflow)
  - output/education_audit.json     (Audit report)
  - output/education_metrics.json   (Performance metrics)
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
    userml_payload,
    verify_integrity,
    verify_negative_proof,
    verify_pii_detachment,
    verify_workflow_isolation,
    generate_audit_report,
)
from jhcontext.pii import InMemoryPIIVault

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def run() -> dict:
    """Execute the education fair assessment scenario. Returns metrics dict."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    # --- Timestamps ---
    base = datetime(2026, 3, 15, 14, 0, 0, tzinfo=timezone.utc)
    ts = {
        "ingestion_start": base,
        "ingestion_end": base + timedelta(seconds=30),
        "grading_start": base + timedelta(minutes=1),
        "grading_end": base + timedelta(minutes=3),
        "equity_start": base + timedelta(minutes=5),
        "equity_end": base + timedelta(minutes=6),
    }

    # =========================================================================
    # STEP 1: Essay Ingestion Agent — separate identity from content
    # =========================================================================
    t0 = time.perf_counter()

    # Grading envelope — scope: education_assessment
    builder = (
        EnvelopeBuilder()
        .set_producer("did:university:ingestion-agent")
        .set_scope("education_assessment")
        .set_ttl("P30D")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(False)
        .set_semantic_payload([
            userml_payload(
                observations=[
                    observation("submission:S-98765", "essay_word_count", 1527),
                    observation("submission:S-98765", "essay_topic", "climate_policy"),
                    observation("submission:S-98765", "rubric_version", "ENG101-v3"),
                ],
            ),
        ])
    )

    # Essay text artifact (content only — no identity)
    builder.add_artifact(
        artifact_id="art-essay-text",
        artifact_type=ArtifactType.TOKEN_SEQUENCE,
        content_hash="sha256:1111aaaa2222bbbb3333cccc4444dddd5555eeee6666ffff7777aaaa8888bbbb",
        model=None,
    )

    # Rubric criteria artifact
    builder.add_artifact(
        artifact_id="art-rubric",
        artifact_type=ArtifactType.TOKEN_SEQUENCE,
        content_hash="sha256:2222bbbb3333cccc4444dddd5555eeee6666ffff7777aaaa8888bbbb9999cccc",
        model=None,
    )

    # NOTE: Identity data is NOT added to this envelope — it goes to the
    # separate equity workflow. This is the foundation of negative proof.

    # PROV: Grading workflow
    prov_grading = PROVGraph("ctx-edu-grading-001")

    prov_grading.add_agent("ingestion-agent", "Essay Ingestion Agent", role="data_processor")
    prov_grading.add_entity("art-essay-text", "Essay Text Content",
                            artifact_type="token_sequence",
                            content_hash="sha256:1111...")
    prov_grading.add_entity("art-rubric", "Grading Rubric (ENG101-v3)",
                            artifact_type="token_sequence",
                            content_hash="sha256:2222...")

    prov_grading.add_activity("essay-ingestion", "Essay Content Extraction",
                              started_at=_iso(ts["ingestion_start"]),
                              ended_at=_iso(ts["ingestion_end"]),
                              method="identity_stripping")
    prov_grading.was_generated_by("art-essay-text", "essay-ingestion")
    prov_grading.was_associated_with("essay-ingestion", "ingestion-agent")

    metrics["ingestion_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 2: AI Grading Agent — grade using ONLY essay + rubric
    # =========================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-grade-result",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:3333cccc4444dddd5555eeee6666ffff7777aaaa8888bbbb9999ccccaaaadddd",
        model="essay-grader-v2",
        confidence=0.91,
    )
    builder.set_passed_artifact("art-grade-result")

    builder.add_decision_influence(
        agent="essay-grading-agent",
        categories=["argument_quality", "evidence_use", "writing_clarity", "critical_thinking"],
        influence_weights={
            "argument_quality": 0.30,
            "evidence_use": 0.30,
            "writing_clarity": 0.20,
            "critical_thinking": 0.20,
        },
        confidence=0.91,
        abstraction_level=AbstractionLevel.INTERPRETATION,
        temporal_scope=TemporalScope.CURRENT,
    )

    # PROV: Grading activity
    prov_grading.add_agent("grading-agent", "AI Essay Grading Agent", role="evaluator")
    prov_grading.add_entity("art-grade-result", "Grading Result (B+, 87/100)",
                            artifact_type="semantic_extraction",
                            content_hash="sha256:3333...")

    prov_grading.add_activity("ai-grading", "AI Essay Evaluation",
                              started_at=_iso(ts["grading_start"]),
                              ended_at=_iso(ts["grading_end"]),
                              method="LLM inference (essay-grader-v2)")
    prov_grading.used("ai-grading", "art-essay-text")
    prov_grading.used("ai-grading", "art-rubric")
    prov_grading.was_generated_by("art-grade-result", "ai-grading")
    prov_grading.was_associated_with("ai-grading", "grading-agent")
    prov_grading.was_derived_from("art-grade-result", "art-essay-text")
    prov_grading.was_derived_from("art-grade-result", "art-rubric")

    metrics["grading_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 3: Equity Reporting Agent — SEPARATE workflow, ISOLATED
    # =========================================================================
    t0 = time.perf_counter()

    # Completely separate PROV graph — no connection to grading
    prov_equity = PROVGraph("ctx-edu-equity-001")

    prov_equity.add_agent("equity-agent", "Equity Reporting Agent", role="compliance_reporter")

    # Identity artifacts live ONLY in the equity workflow
    prov_equity.add_entity("art-student-identity", "Student Identity Data",
                           artifact_type="biometric",
                           content_hash="sha256:identity-hash-redacted")
    prov_equity.add_entity("art-demographic-attrs", "Demographic Attributes",
                           artifact_type="sensitive",
                           content_hash="sha256:demographics-hash-redacted")
    prov_equity.add_entity("art-equity-report", "Aggregate Equity Statistics",
                           artifact_type="semantic_extraction")

    prov_equity.add_activity("equity-reporting", "Aggregate Demographic Statistics",
                             started_at=_iso(ts["equity_start"]),
                             ended_at=_iso(ts["equity_end"]),
                             method="aggregate_statistics")
    prov_equity.used("equity-reporting", "art-student-identity")
    prov_equity.used("equity-reporting", "art-demographic-attrs")
    prov_equity.was_generated_by("art-equity-report", "equity-reporting")
    prov_equity.was_associated_with("equity-reporting", "equity-agent")
    prov_equity.was_derived_from("art-equity-report", "art-student-identity")
    prov_equity.was_derived_from("art-equity-report", "art-demographic-attrs")

    metrics["equity_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 4: Build, sign, and finalize envelope (with PII detachment)
    # =========================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()

    builder.set_privacy(
        data_category="behavioral",
        legal_basis="legitimate_interest",
        retention="P1Y",
        storage_policy="university-encrypted",
        feature_suppression=["student_name", "student_id", "demographic_attributes"],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=False,
        model_card_ref="https://university.example/models/essay-grader-v2",
        test_suite_ref="https://university.example/fairness-tests/2026-Q1",
        escalation_path="academic-affairs@university.example",
    )

    # Enable PII detachment — tokenizes student identifiers before signing
    builder.enable_pii_detachment(vault=pii_vault)

    # Attach provenance reference BEFORE signing
    grading_digest = prov_grading.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov_grading.context_id}"
    builder._envelope.provenance_ref.prov_digest = grading_digest

    envelope = builder.sign("did:university:compliance-officer").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 5: Audit — verify Article 13 compliance
    # =========================================================================
    t0 = time.perf_counter()

    # 5a. Negative proof: identity/biometric data NOT in grading chain
    negative_result = verify_negative_proof(
        prov_grading,
        decision_entity_id="art-grade-result",
        excluded_artifact_types=["biometric", "sensitive"],
    )

    # 5b. Workflow isolation: grading and equity share zero artifacts
    isolation_result = verify_workflow_isolation(prov_grading, prov_equity)

    # 5c. PII detachment check
    pii_result = verify_pii_detachment(envelope)

    # 5d. Integrity check
    integrity_result = verify_integrity(envelope)

    # 5e. Generate report
    report = generate_audit_report(
        envelope, prov_grading,
        [negative_result, isolation_result, pii_result, integrity_result],
    )

    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =========================================================================
    # STEP 6: Save outputs
    # =========================================================================

    # Envelope JSON-LD
    envelope_path = OUTPUT_DIR / "education_envelope.json"
    envelope_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # PROV Turtle — grading workflow
    grading_prov_path = OUTPUT_DIR / "education_prov.ttl"
    grading_prov_path.write_text(prov_grading.serialize("turtle"), encoding="utf-8")

    # PROV Turtle — equity workflow (separate)
    equity_prov_path = OUTPUT_DIR / "education_equity_prov.ttl"
    equity_prov_path.write_text(prov_equity.serialize("turtle"), encoding="utf-8")

    # Audit report
    audit_path = OUTPUT_DIR / "education_audit.json"
    audit_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Metrics
    grading_entities = prov_grading.get_all_entities()
    grading_activities = prov_grading.get_temporal_sequence()
    equity_entities = prov_equity.get_all_entities()
    metrics["grading_prov_entities"] = len(grading_entities)
    metrics["grading_prov_activities"] = len(grading_activities)
    metrics["equity_prov_entities"] = len(equity_entities)
    metrics["envelope_size_bytes"] = envelope_path.stat().st_size
    metrics["grading_prov_size_bytes"] = grading_prov_path.stat().st_size
    metrics["equity_prov_size_bytes"] = equity_prov_path.stat().st_size

    metrics_path = OUTPUT_DIR / "education_metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )

    # =========================================================================
    # Print summary
    # =========================================================================
    print("=" * 60)
    print("EDUCATION SCENARIO — Article 13 Negative Proof")
    print("=" * 60)
    print(f"  Context ID:          {envelope.context_id}")
    print(f"  Producer:            {envelope.producer}")
    print(f"  Risk Level:          {envelope.compliance.risk_level.value}")
    print(f"  Artifacts:           {len(envelope.artifacts_registry)}")
    print(f"  Grading Entities:    {metrics['grading_prov_entities']}")
    print(f"  Grading Activities:  {metrics['grading_prov_activities']}")
    print(f"  Equity Entities:     {metrics['equity_prov_entities']}")
    print()
    print("  Grading Dependency Chain:")
    chain = prov_grading.get_entities_in_chain("art-grade-result")
    for e in sorted(chain):
        print(f"    - {e}")
    print()
    print("  AUDIT RESULTS:")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall: {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print("  Performance:")
    print(f"    Ingestion:   {metrics['ingestion_ms']:.1f} ms")
    print(f"    Grading:     {metrics['grading_ms']:.1f} ms")
    print(f"    Equity:      {metrics['equity_ms']:.1f} ms")
    print(f"    Envelope:    {metrics['envelope_build_ms']:.1f} ms")
    print(f"    Audit:       {metrics['audit_ms']:.1f} ms")
    print(f"    Total:       {metrics['total_ms']:.1f} ms")
    print()
    print(f"  Outputs saved to: {OUTPUT_DIR}/")
    print("=" * 60)

    return metrics


if __name__ == "__main__":
    run()
