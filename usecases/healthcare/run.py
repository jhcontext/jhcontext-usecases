"""Healthcare Scenario: Verifying Meaningful Human Oversight (EU AI Act Article 14).

A hospital uses AI to recommend treatment plans for oncology patients.
This scenario demonstrates how PAC-AI proves that a physician performed
meaningful oversight — not just superficial approval-clicking.

Pipeline: Sensor → Situation Recognition → Decision → Human Oversight → Audit

Outputs:
  - output/healthcare_envelope.json  (JSON-LD envelope)
  - output/healthcare_prov.ttl       (W3C PROV graph in Turtle)
  - output/healthcare_audit.json     (Audit report)
  - output/healthcare_metrics.json   (Performance metrics)
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
    situation,
    userml_payload,
    verify_integrity,
    verify_temporal_oversight,
    generate_audit_report,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def run() -> dict:
    """Execute the healthcare human oversight scenario. Returns metrics dict."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    # --- Timestamps (simulated real clinical timeline) ---
    base = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
    ts = {
        "sensor_start": base,
        "sensor_end": base + timedelta(minutes=2),
        "situation_start": base + timedelta(minutes=3),
        "situation_end": base + timedelta(minutes=5),
        "decision_start": base + timedelta(minutes=6),
        "decision_end": base + timedelta(minutes=7),
        "oversight_start": base + timedelta(minutes=10),
        "ct_review_end": base + timedelta(minutes=14),        # 4 min CT review
        "history_review_end": base + timedelta(minutes=17),    # 3 min history review
        "pathology_review_end": base + timedelta(minutes=19),  # 2 min pathology
        "ai_review_end": base + timedelta(minutes=20),         # 1 min AI review
        "override_time": base + timedelta(minutes=20, seconds=30),
    }

    # =========================================================================
    # STEP 1: Sensor Agent — collect clinical observations
    # =========================================================================
    t0 = time.perf_counter()

    sensor_payload = userml_payload(
        observations=[
            observation("patient:P-12345", "age", 62),
            observation("patient:P-12345", "gender", "M"),
            observation("patient:P-12345", "tumor_marker_CEA", 8.7),
            observation("patient:P-12345", "tumor_marker_CA19_9", 42.0),
            observation("patient:P-12345", "hemoglobin", 11.2),
            observation("patient:P-12345", "wbc_count", 6800),
            observation("patient:P-12345", "ct_scan_ref", "img:CT-2026-03-15-P12345"),
        ],
    )

    builder = (
        EnvelopeBuilder()
        .set_producer("did:hospital:sensor-agent")
        .set_scope("healthcare_treatment_recommendation")
        .set_ttl("PT24H")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([sensor_payload])
        .add_artifact(
            artifact_id="art-demographics",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            model=None,
        )
        .add_artifact(
            artifact_id="art-lab-results",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
            model=None,
        )
        .add_artifact(
            artifact_id="art-ct-scan-meta",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
            storage_ref="pacs://hospital/CT-2026-03-15-P12345",
        )
    )

    # Build PROV graph
    prov = PROVGraph("ctx-health-001")

    # Sensor entities + activity
    prov.add_agent("sensor-agent", "Clinical Sensor Agent", role="data_collector")
    prov.add_entity("art-demographics", "Patient Demographics",
                    artifact_type="token_sequence",
                    content_hash="sha256:a1b2...")
    prov.add_entity("art-lab-results", "Lab Results",
                    artifact_type="token_sequence",
                    content_hash="sha256:b2c3...")
    prov.add_entity("art-ct-scan-meta", "CT Scan Metadata",
                    artifact_type="token_sequence",
                    content_hash="sha256:c3d4...")

    prov.add_activity("sensor-collection", "Clinical Data Collection",
                      started_at=_iso(ts["sensor_start"]),
                      ended_at=_iso(ts["sensor_end"]),
                      method="EHR/PACS integration")
    prov.was_generated_by("art-demographics", "sensor-collection")
    prov.was_generated_by("art-lab-results", "sensor-collection")
    prov.was_generated_by("art-ct-scan-meta", "sensor-collection")
    prov.was_associated_with("sensor-collection", "sensor-agent")

    metrics["sensor_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 2: Situation Recognition Agent — interpret observations
    # =========================================================================
    t0 = time.perf_counter()

    situation_payload = userml_payload(
        interpretations=[
            interpretation("patient:P-12345", "riskLevel", "high", confidence=0.93),
            interpretation("patient:P-12345", "tumorResponse", "partial_response", confidence=0.88),
        ],
        situations=[
            situation("patient:P-12345", "post-operative-day-3",
                      start=_iso(base - timedelta(days=3)), confidence=0.95),
        ],
    )
    builder.set_semantic_payload([sensor_payload, situation_payload])

    builder.add_artifact(
        artifact_id="art-semantic-extraction",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
        model="clinical-situation-v2",
        confidence=0.93,
    )
    builder.set_passed_artifact("art-semantic-extraction")

    # PROV: situation recognition
    prov.add_agent("situation-agent", "Situation Recognition Agent", role="interpreter")
    prov.add_entity("art-semantic-extraction", "Clinical Situation Extraction",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:d4e5...")
    prov.add_activity("situation-recognition", "Clinical Situation Analysis",
                      started_at=_iso(ts["situation_start"]),
                      ended_at=_iso(ts["situation_end"]),
                      method="LLM inference (clinical-situation-v2)")
    prov.used("situation-recognition", "art-demographics")
    prov.used("situation-recognition", "art-lab-results")
    prov.used("situation-recognition", "art-ct-scan-meta")
    prov.was_generated_by("art-semantic-extraction", "situation-recognition")
    prov.was_associated_with("situation-recognition", "situation-agent")
    prov.was_derived_from("art-semantic-extraction", "art-demographics")
    prov.was_derived_from("art-semantic-extraction", "art-lab-results")
    prov.was_derived_from("art-semantic-extraction", "art-ct-scan-meta")

    metrics["situation_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 3: Decision Agent — generate treatment recommendation
    # =========================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-recommendation",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
        model="oncology-decision-v3",
        confidence=0.87,
    )
    builder.add_decision_influence(
        agent="oncology-decision-agent",
        categories=["patient_status", "tumor_response", "lab_values"],
        influence_weights={"patient_status": 0.95, "tumor_response": 0.80, "lab_values": 0.65},
        confidence=0.87,
        abstraction_level=AbstractionLevel.SITUATION,
        temporal_scope=TemporalScope.CURRENT,
    )

    # PROV: decision
    prov.add_agent("decision-agent", "Treatment Recommendation Agent", role="decision_maker")
    prov.add_entity("art-recommendation", "AI Treatment Recommendation",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:e5f6...")
    prov.add_activity("ai-recommendation", "AI Treatment Analysis",
                      started_at=_iso(ts["decision_start"]),
                      ended_at=_iso(ts["decision_end"]),
                      method="LLM inference (oncology-decision-v3)")
    prov.used("ai-recommendation", "art-semantic-extraction")
    prov.was_generated_by("art-recommendation", "ai-recommendation")
    prov.was_associated_with("ai-recommendation", "decision-agent")
    prov.was_derived_from("art-recommendation", "art-semantic-extraction")

    metrics["decision_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 4: Human Oversight Agent — physician review with timing
    # =========================================================================
    t0 = time.perf_counter()

    prov.add_agent("dr-chen", "Dr. Chen (Oncologist)", role="human_oversight")

    # 4a. Review CT scan images (4 minutes)
    prov.add_activity("review-ct-scan", "Physician Reviews CT Scan Images",
                      started_at=_iso(ts["oversight_start"]),
                      ended_at=_iso(ts["ct_review_end"]))
    prov.used("review-ct-scan", "art-ct-scan-meta")
    prov.was_associated_with("review-ct-scan", "dr-chen")

    # 4b. Review treatment history (3 minutes)
    prov.add_activity("review-history", "Physician Reviews Treatment History",
                      started_at=_iso(ts["ct_review_end"]),
                      ended_at=_iso(ts["history_review_end"]))
    prov.used("review-history", "art-demographics")
    prov.used("review-history", "art-lab-results")
    prov.was_associated_with("review-history", "dr-chen")

    # 4c. Review pathology report (2 minutes)
    prov.add_activity("review-pathology", "Physician Reviews Pathology Report",
                      started_at=_iso(ts["history_review_end"]),
                      ended_at=_iso(ts["pathology_review_end"]))
    prov.used("review-pathology", "art-lab-results")
    prov.was_associated_with("review-pathology", "dr-chen")

    # 4d. Review AI recommendation (1 minute)
    prov.add_activity("review-ai-recommendation", "Physician Reviews AI Recommendation",
                      started_at=_iso(ts["pathology_review_end"]),
                      ended_at=_iso(ts["ai_review_end"]))
    prov.used("review-ai-recommendation", "art-recommendation")
    prov.was_associated_with("review-ai-recommendation", "dr-chen")

    # 4e. Override decision
    prov.add_entity("art-final-decision", "Physician Override Decision",
                    artifact_type="semantic_extraction",
                    generated_at=_iso(ts["override_time"]))
    prov.add_activity("physician-override", "Physician Overrides AI Recommendation",
                      started_at=_iso(ts["ai_review_end"]),
                      ended_at=_iso(ts["override_time"]),
                      method="clinical_judgment")
    prov.was_generated_by("art-final-decision", "physician-override")
    prov.was_associated_with("physician-override", "dr-chen")
    prov.was_derived_from("art-final-decision", "art-recommendation")
    prov.was_informed_by("physician-override", "review-ct-scan")
    prov.was_informed_by("physician-override", "review-history")
    prov.was_informed_by("physician-override", "review-pathology")
    prov.was_informed_by("physician-override", "review-ai-recommendation")

    # Add final decision artifact to envelope
    builder.add_artifact(
        artifact_id="art-final-decision",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1",
        model=None,
        confidence=1.0,
    )

    metrics["oversight_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 5: Build, sign, and finalize envelope
    # =========================================================================
    t0 = time.perf_counter()

    builder.set_privacy(
        data_category="sensitive",
        legal_basis="vital_interest",
        retention="P2Y",
        storage_policy="hospital-encrypted",
        feature_suppression=["patient_name", "patient_address"],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://hospital.example/models/oncology-v3",
        escalation_path="oncology-dept-head@hospital.example",
    )

    # Attach provenance reference BEFORE signing
    prov_digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest

    envelope = builder.sign("did:hospital:compliance-officer").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 6: Audit — verify Article 14 compliance
    # =========================================================================
    t0 = time.perf_counter()

    # 6a. Temporal oversight check
    human_activities = [
        "review-ct-scan",
        "review-history",
        "review-pathology",
        "review-ai-recommendation",
    ]
    temporal_result = verify_temporal_oversight(
        prov,
        ai_activity_id="ai-recommendation",
        human_activities=human_activities,
        min_review_seconds=300.0,  # 5 minutes minimum
    )

    # 6b. Integrity check
    integrity_result = verify_integrity(envelope)

    # 6c. Generate report
    report = generate_audit_report(
        envelope, prov,
        [temporal_result, integrity_result],
    )

    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =========================================================================
    # STEP 7: Save outputs
    # =========================================================================

    # Envelope JSON-LD
    envelope_path = OUTPUT_DIR / "healthcare_envelope.json"
    envelope_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # PROV Turtle
    prov_path = OUTPUT_DIR / "healthcare_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")

    # Audit report
    audit_path = OUTPUT_DIR / "healthcare_audit.json"
    audit_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Metrics
    prov_entities = prov.get_all_entities()
    prov_sequence = prov.get_temporal_sequence()
    metrics["prov_entities"] = len(prov_entities)
    metrics["prov_activities"] = len(prov_sequence)
    metrics["envelope_size_bytes"] = envelope_path.stat().st_size
    metrics["prov_size_bytes"] = prov_path.stat().st_size

    metrics_path = OUTPUT_DIR / "healthcare_metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )

    # =========================================================================
    # Print summary
    # =========================================================================
    print("=" * 60)
    print("HEALTHCARE SCENARIO — Article 14 Temporal Oversight")
    print("=" * 60)
    print(f"  Context ID:        {envelope.context_id}")
    print(f"  Producer:          {envelope.producer}")
    print(f"  Risk Level:        {envelope.compliance.risk_level.value}")
    print(f"  Artifacts:         {len(envelope.artifacts_registry)}")
    print(f"  PROV Entities:     {metrics['prov_entities']}")
    print(f"  PROV Activities:   {metrics['prov_activities']}")
    print()
    print("  Temporal Sequence:")
    for act in prov_sequence:
        print(f"    {act['started_at'][:19]}  {act['label']}")
    print()
    print("  AUDIT RESULTS:")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall: {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print("  Performance:")
    print(f"    Sensor:      {metrics['sensor_ms']:.1f} ms")
    print(f"    Situation:   {metrics['situation_ms']:.1f} ms")
    print(f"    Decision:    {metrics['decision_ms']:.1f} ms")
    print(f"    Oversight:   {metrics['oversight_ms']:.1f} ms")
    print(f"    Envelope:    {metrics['envelope_build_ms']:.1f} ms")
    print(f"    Audit:       {metrics['audit_ms']:.1f} ms")
    print(f"    Total:       {metrics['total_ms']:.1f} ms")
    print()
    print(f"  Outputs saved to: {OUTPUT_DIR}/")
    print("=" * 60)

    return metrics


if __name__ == "__main__":
    run()
