"""Community-Health-Worker Mental-Health Screening.

Task-shifted LMIC mental-health screening: a CHW operates a tablet that runs
a four-agent pipeline; supervision is by a scarce district specialist via an
asynchronous queue.

Pipeline: interview_assistance (PHQ-9) -> risk_classification -> referral ->
  supervisor_review. CHW visits routinely occur offline; envelopes queue
  on-device for sync on return.

Verifiers exercised:
  - verify_temporal_oversight (specialist async review after AI classification)
  - verify_negative_proof     (variables the programme agreed not to consider)
  - verify_pii_detachment     (patient responses in identity vault, tokens in graph)
  - verify_integrity

Outputs (usecases/output/):
  chw_mental_health_envelope.json
  chw_mental_health_prov.ttl
  chw_mental_health_audit.json
  chw_mental_health_metrics.json
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
    verify_negative_proof,
    verify_pii_detachment,
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

    # ----- Simulated CHW home visit + async supervisor review the next day -----
    base = datetime(2026, 4, 18, 9, 15, 0, tzinfo=timezone.utc)
    ts = {
        "interview_start": base,
        "interview_end": base + timedelta(minutes=18),     # PHQ-9 elicitation
        "classify_start": base + timedelta(minutes=18, seconds=30),
        "classify_end": base + timedelta(minutes=19),
        "referral_start": base + timedelta(minutes=19, seconds=30),
        "referral_end": base + timedelta(minutes=20),
        # CHW returns to clinic -> opportunistic sync -> supervisor queue
        "sync_time": base + timedelta(hours=4),
        # District specialist reviews the next morning
        "supervisor_start": base + timedelta(days=1, hours=1),
        "supervisor_end": base + timedelta(days=1, hours=1, minutes=8),  # 8-min review
    }

    # =====================================================================
    # STEP 1: Interview-assistance agent — PHQ-9 structured elicitation
    # Raw responses go to the identity vault (via PII detachment).
    # =====================================================================
    t0 = time.perf_counter()

    interview_payload = userml_payload(
        observations=[
            observation("patient:P-CHW0042", "phq9_total", 19),
            observation("patient:P-CHW0042", "phq9_item_9", 2),  # suicidal ideation
            observation("patient:P-CHW0042", "instrument_version", "PHQ-9-v1"),
            observation("patient:P-CHW0042", "language", "sw-TZ"),
        ],
        interpretations=[
            interpretation("patient:P-CHW0042", "phq9_severity",
                           "moderately_severe", confidence=0.96),
            interpretation("patient:P-CHW0042", "suicide_flag",
                           True, confidence=1.0),
        ],
    )

    builder = (
        EnvelopeBuilder()
        .set_producer("did:chw:interview-agent")
        .set_scope("chw_mental_health_screening")
        .set_ttl("PT24H")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([interview_payload])
        .add_artifact(
            artifact_id="art-phq9-responses",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:a1f9b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1",
            model=None,
            storage_ref="vault://identity/P-CHW0042/phq9-2026-04-18",
        )
        .add_artifact(
            artifact_id="art-interview-structured",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:b2e8a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
            model="phq9-interviewer-v1.1",
            confidence=0.96,
        )
    )

    prov = PROVGraph("ctx-chw-mh-001")
    prov.add_agent("interview-agent", "Interview Assistance Agent",
                   role="elicitor")
    prov.add_entity("art-phq9-responses",
                    "PHQ-9 raw responses (vaulted)",
                    artifact_type="token_sequence",
                    content_hash="sha256:a1f9...")
    prov.add_entity("art-interview-structured",
                    "Structured PHQ-9 interpretation (total=19, suicide_flag=true)",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:b2e8...")
    prov.add_activity("phq9-interview", "PHQ-9 structured elicitation",
                      started_at=_iso(ts["interview_start"]),
                      ended_at=_iso(ts["interview_end"]),
                      method="on-tablet PHQ-9-v1 instrument")
    prov.was_generated_by("art-phq9-responses", "phq9-interview")
    prov.was_generated_by("art-interview-structured", "phq9-interview")
    prov.was_associated_with("phq9-interview", "interview-agent")
    prov.was_derived_from("art-interview-structured", "art-phq9-responses")

    metrics["interview_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 2: Risk-classification agent — severity + suicide-risk triage
    # =====================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-risk-classification",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:c393a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
        model="mh-risk-classifier-v2.0",
        confidence=0.92,
    )
    builder.add_decision_influence(
        agent="risk-classifier",
        categories=["phq9_severity", "suicide_item"],
        influence_weights={"phq9_severity": 0.7, "suicide_item": 0.3},
        confidence=0.92,
        abstraction_level=AbstractionLevel.SITUATION,
        temporal_scope=TemporalScope.CURRENT,
    )

    prov.add_agent("risk-classifier", "Risk Classification Agent", role="classifier")
    prov.add_entity("art-risk-classification",
                    "Severity=moderately_severe; suicide_risk=elevated",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:c393...")
    prov.add_activity("risk-classification",
                      "Severity + suicide-risk triage",
                      started_at=_iso(ts["classify_start"]),
                      ended_at=_iso(ts["classify_end"]),
                      method="LLM inference (mh-risk-classifier-v2.0)")
    prov.used("risk-classification", "art-interview-structured")
    prov.was_generated_by("art-risk-classification", "risk-classification")
    prov.was_associated_with("risk-classification", "risk-classifier")
    prov.was_derived_from("art-risk-classification", "art-interview-structured")

    metrics["classify_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 3: Referral-recommendation agent (triggers mandatory specialist queue)
    # =====================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-referral",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:d404a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
        model="mh-referral-rules-v0.8",
        confidence=1.0,
    )

    prov.add_agent("referral-agent", "Referral Recommendation Agent",
                   role="recommender")
    prov.add_entity("art-referral",
                    "Referral: same-week specialist review (mandatory)",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:d404...")
    prov.add_activity("referral-recommendation",
                      "Referral recommendation + supervisor queue enqueue",
                      started_at=_iso(ts["referral_start"]),
                      ended_at=_iso(ts["referral_end"]),
                      method="rule-based")
    prov.used("referral-recommendation", "art-risk-classification")
    prov.was_generated_by("art-referral", "referral-recommendation")
    prov.was_associated_with("referral-recommendation", "referral-agent")
    prov.was_derived_from("art-referral", "art-risk-classification")

    builder.set_passed_artifact("art-referral")

    metrics["referral_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 4: Supervisor (district specialist) async review
    # =====================================================================
    t0 = time.perf_counter()

    prov.add_agent("dr-matumbo", "Dr. Matumbo (district mental-health specialist)",
                   role="human_oversight")
    prov.add_activity("supervisor-review",
                      "Specialist reviews PHQ-9, risk classification, referral",
                      started_at=_iso(ts["supervisor_start"]),
                      ended_at=_iso(ts["supervisor_end"]))
    prov.used("supervisor-review", "art-interview-structured")
    prov.used("supervisor-review", "art-risk-classification")
    prov.used("supervisor-review", "art-referral")
    prov.was_associated_with("supervisor-review", "dr-matumbo")

    metrics["oversight_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 5: Finalise envelope (identity-vault tokens via PII detachment)
    # =====================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()
    builder.set_privacy(
        data_category="sensitive",
        legal_basis="informed_consent",
        retention="P2Y",
        storage_policy="offline-chw-tablet + encrypted vault",
        feature_suppression=[
            # Variables the programme agreed not to consider (negative-proof target)
            "ethnicity", "religion", "household_income",
        ],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://district.example/models/mh-risk-classifier-v2.0",
        escalation_path="mh-specialist-on-call@district.example",
    )
    builder.enable_pii_detachment(vault=pii_vault)

    prov_digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest
    envelope = builder.sign("did:district:compliance-officer").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 6: Audit
    # =====================================================================
    t0 = time.perf_counter()

    temporal = verify_temporal_oversight(
        prov,
        ai_activity_id="risk-classification",
        human_activities=["supervisor-review"],
        min_review_seconds=300.0,
    )
    negative = verify_negative_proof(
        prov,
        decision_entity_id="art-risk-classification",
        excluded_artifact_types=["ethnicity", "religion", "household_income"],
    )
    integrity = verify_integrity(envelope)
    pii = verify_pii_detachment(envelope)

    report = generate_audit_report(envelope, prov,
                                   [temporal, negative, integrity, pii])

    # Programme-evaluation metric: time-to-review (paper §5.3 last sentence)
    review_delay_hours = (
        ts["supervisor_end"] - ts["referral_end"]
    ).total_seconds() / 3600.0
    metrics["time_to_review_hours"] = review_delay_hours

    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =====================================================================
    # STEP 7: Save outputs
    # =====================================================================
    env_path = OUTPUT_DIR / "chw_mental_health_envelope.json"
    env_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    prov_path = OUTPUT_DIR / "chw_mental_health_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")
    audit_path = OUTPUT_DIR / "chw_mental_health_audit.json"
    audit_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics["prov_entities"] = len(prov.get_all_entities())
    metrics["prov_activities"] = len(prov.get_temporal_sequence())
    metrics["envelope_size_bytes"] = env_path.stat().st_size
    metrics_path = OUTPUT_DIR / "chw_mental_health_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # ----- Console summary -----
    print("=" * 64)
    print("Healthcare Scenario 3 - CHW Mental-Health Screening")
    print("=" * 64)
    print(f"  Context ID:      {envelope.context_id}")
    print(f"  TTL:             {envelope.ttl}")
    print(f"  Risk Level:      {envelope.compliance.risk_level.value}")
    print(f"  Artifacts:       {len(envelope.artifacts_registry)}")
    print(f"  PROV entities:   {metrics['prov_entities']}")
    print(f"  PROV activities: {metrics['prov_activities']}")
    print(f"  Time-to-review:  {review_delay_hours:.1f} hours (auditable metric)")
    print()
    print("  Audit (Arts. 12 / 14 / 15 + PII):")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall:         {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print(f"  Total runtime:   {metrics['total_ms']:.1f} ms")
    print(f"  Outputs:         {OUTPUT_DIR}/chw_mental_health_*")
    print("=" * 64)
    return metrics


if __name__ == "__main__":
    run()
