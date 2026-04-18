"""Rural Emergency Cardiac Triage.

Resource-constrained healthcare scenario: a district hospital runs a
multi-agent AI triage pipeline on an edge device with intermittent uplink;
teleconsult-specialist oversight runs asynchronously over a high-risk
decision. The emitted envelope at the physiological-signal -> triage
handoff matches the protocol's reference listing.

Pipeline: physiological_signal -> triage_classification -> resource_allocation
  on an edge device. Intermittent uplink. 15-minute TTL. High-risk.

Verifiers exercised:
  - verify_temporal_oversight (teleconsult specialist review after AI triage)
  - verify_negative_proof     (insurance + socio-economic indicators absent)
  - verify_integrity          (envelope signature + content-hash)

Outputs (usecases/output/):
  triage_rural_envelope.json     JSON-LD envelope
  triage_rural_prov.ttl          W3C PROV graph (Turtle)
  triage_rural_audit.json        Audit report
  triage_rural_metrics.json      Timing metrics
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

    # ----- Simulated clinical timeline -----
    base = datetime(2026, 4, 18, 14, 2, 17, tzinfo=timezone.utc)
    ts = {
        "physio_start": base,
        "physio_end": base + timedelta(seconds=30),
        "triage_start": base + timedelta(seconds=45),
        "triage_end": base + timedelta(seconds=60),
        "allocation_start": base + timedelta(seconds=75),
        "allocation_end": base + timedelta(seconds=90),
        # Teleconsult specialist review (after uplink recovers)
        "teleconsult_start": base + timedelta(minutes=8),
        "teleconsult_end": base + timedelta(minutes=14),  # 6-min review
    }

    # =====================================================================
    # STEP 1: Physiological-signal agent (ECG, BP, SpO2)
    # =====================================================================
    t0 = time.perf_counter()

    physio_payload = userml_payload(
        observations=[
            observation("patient:P-R042", "ecg_ref", "sig:ECG-2026-04-18-R042"),
            observation("patient:P-R042", "bp_systolic", 148),
            observation("patient:P-R042", "bp_diastolic", 92),
            observation("patient:P-R042", "spo2", 94),
        ],
        interpretations=[
            interpretation("patient:P-R042", "finding", "suspected_AF", confidence=0.87),
        ],
    )

    builder = (
        EnvelopeBuilder()
        .set_producer("did:hospital:physio-signal-agent")
        .set_scope("rural_cardiac_triage")
        .set_ttl("PT15M")  # 15-minute TTL for emergency risk scores
        .set_risk_level(RiskLevel.HIGH)  # auto-sets forwarding_policy=SEMANTIC_FORWARD
        .set_human_oversight(True)
        .set_semantic_payload([physio_payload])
        .add_artifact(
            artifact_id="art-ecg-embedding",
            artifact_type=ArtifactType.EMBEDDING,
            content_hash="sha256:3f9c4a81b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
            model="ecg-classifier-v2.4",
            deterministic=False,
        )
        .add_artifact(
            artifact_id="art-semantic-extraction",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
            model="ecg-classifier-v2.4",
            confidence=0.87,
        )
        .set_passed_artifact("art-semantic-extraction")
    )

    # PROV graph
    prov = PROVGraph("ctx-triage-rural-001")
    prov.add_agent("physio-signal-agent", "Physiological Signal Agent", role="sensor")
    prov.add_entity("art-ecg-embedding", "Raw ECG Embedding",
                    artifact_type="embedding", content_hash="sha256:3f9c...")
    prov.add_entity("art-semantic-extraction", "Suspected AF (conf=0.87)",
                    artifact_type="semantic_extraction", content_hash="sha256:d4e5...")
    prov.add_activity("physio-collection", "ECG + vitals acquisition",
                      started_at=_iso(ts["physio_start"]),
                      ended_at=_iso(ts["physio_end"]),
                      method="edge-device sensors")
    prov.was_generated_by("art-ecg-embedding", "physio-collection")
    prov.was_generated_by("art-semantic-extraction", "physio-collection")
    prov.was_associated_with("physio-collection", "physio-signal-agent")
    prov.was_derived_from("art-semantic-extraction", "art-ecg-embedding")

    metrics["physio_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 2: Triage-classification agent (priority assignment)
    # =====================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-triage-priority",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
        model="triage-classifier-v1.8",
        confidence=0.91,
    )
    builder.add_decision_influence(
        agent="triage-agent",
        categories=["ecg_finding", "vital_signs"],
        influence_weights={"ecg_finding": 0.75, "vital_signs": 0.25},
        confidence=0.91,
        abstraction_level=AbstractionLevel.SITUATION,
        temporal_scope=TemporalScope.CURRENT,
    )

    prov.add_agent("triage-agent", "Triage Classification Agent", role="classifier")
    prov.add_entity("art-triage-priority", "Triage Priority: P1 (cardiac, urgent)",
                    artifact_type="semantic_extraction", content_hash="sha256:e5f6...")
    prov.add_activity("triage-classification", "Triage priority assignment",
                      started_at=_iso(ts["triage_start"]),
                      ended_at=_iso(ts["triage_end"]),
                      method="LLM inference (triage-classifier-v1.8)")
    prov.used("triage-classification", "art-semantic-extraction")
    prov.was_generated_by("art-triage-priority", "triage-classification")
    prov.was_associated_with("triage-classification", "triage-agent")
    prov.was_derived_from("art-triage-priority", "art-semantic-extraction")

    metrics["triage_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 3: Resource-allocation agent (bed + specialist page)
    # =====================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-allocation",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        model="resource-allocator-v1.0",
        confidence=1.0,
    )

    prov.add_agent("resource-agent", "Resource Allocation Agent", role="scheduler")
    prov.add_entity("art-allocation",
                    "Bed=CCU-3; specialist=teleconsult:cardiology-duty",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:a1b2...")
    prov.add_activity("resource-allocation", "Bed + specialist allocation",
                      started_at=_iso(ts["allocation_start"]),
                      ended_at=_iso(ts["allocation_end"]),
                      method="rule-based")
    prov.used("resource-allocation", "art-triage-priority")
    prov.was_generated_by("art-allocation", "resource-allocation")
    prov.was_associated_with("resource-allocation", "resource-agent")
    prov.was_derived_from("art-allocation", "art-triage-priority")

    metrics["allocation_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 4: Teleconsult specialist review (async, after uplink recovers)
    # =====================================================================
    t0 = time.perf_counter()

    prov.add_agent("dr-rivera", "Dr. Rivera (Cardiology, teleconsult)",
                   role="human_oversight")
    prov.add_activity("teleconsult-review",
                      "Specialist reviews ECG + triage priority",
                      started_at=_iso(ts["teleconsult_start"]),
                      ended_at=_iso(ts["teleconsult_end"]))
    prov.used("teleconsult-review", "art-ecg-embedding")
    prov.used("teleconsult-review", "art-semantic-extraction")
    prov.used("teleconsult-review", "art-triage-priority")
    prov.was_associated_with("teleconsult-review", "dr-rivera")

    metrics["oversight_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 5: Build, detach PII, sign envelope
    # =====================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()
    # Paper's envelope uses data_category="sensitive_clinical" (§3.1 tier);
    # SDK v0.3.4 DataCategory enum has {behavioural, biometric, sensitive}.
    # We use the generic "sensitive" tier here until clinical-specific tiers land.
    builder.set_privacy(
        data_category="sensitive",
        legal_basis="art_9_2_h_gdpr",  # healthcare-provision lawful basis
        retention="P30D",
        storage_policy="edge-encrypted",
        feature_suppression=["insurance_status", "socio_economic_indicator"],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://hospital.example/models/ecg-classifier-v2.4",
        escalation_path="cardiology-on-call@hospital.example",
    )
    builder.enable_pii_detachment(vault=pii_vault)

    prov_digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest

    envelope = builder.sign("did:hospital:compliance-officer").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 6: Audit (Art. 12 integrity, Art. 14 oversight, Art. 15 neg-proof)
    # =====================================================================
    t0 = time.perf_counter()

    temporal = verify_temporal_oversight(
        prov,
        ai_activity_id="triage-classification",
        human_activities=["teleconsult-review"],
        min_review_seconds=300.0,  # 5 minutes minimum
    )
    negative = verify_negative_proof(
        prov,
        decision_entity_id="art-triage-priority",
        excluded_artifact_types=["insurance_record", "socio_economic_indicator"],
    )
    integrity = verify_integrity(envelope)
    pii = verify_pii_detachment(envelope)

    report = generate_audit_report(envelope, prov, [temporal, negative, integrity, pii])
    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =====================================================================
    # STEP 7: Save outputs
    # =====================================================================
    env_path = OUTPUT_DIR / "triage_rural_envelope.json"
    env_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    prov_path = OUTPUT_DIR / "triage_rural_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")

    audit_path = OUTPUT_DIR / "triage_rural_audit.json"
    audit_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics["prov_entities"] = len(prov.get_all_entities())
    metrics["prov_activities"] = len(prov.get_temporal_sequence())
    metrics["envelope_size_bytes"] = env_path.stat().st_size
    metrics_path = OUTPUT_DIR / "triage_rural_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # ----- Console summary -----
    print("=" * 64)
    print("Healthcare Scenario 1 - Rural Emergency Cardiac Triage")
    print("=" * 64)
    print(f"  Context ID:      {envelope.context_id}")
    print(f"  TTL:             {envelope.ttl}  (emergency window)")
    print(f"  Risk Level:      {envelope.compliance.risk_level.value}")
    print(f"  Forwarding:      {envelope.compliance.forwarding_policy.value}")
    print(f"  Artifacts:       {len(envelope.artifacts_registry)}")
    print(f"  PROV entities:   {metrics['prov_entities']}")
    print(f"  PROV activities: {metrics['prov_activities']}")
    print()
    print("  Audit (Arts. 12 / 14 / 15 + PII):")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall:         {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print(f"  Total runtime:   {metrics['total_ms']:.1f} ms")
    print(f"  Outputs:         {OUTPUT_DIR}/triage_rural_*")
    print("=" * 64)
    return metrics


if __name__ == "__main__":
    run()
