"""Chronic-Disease Remote Monitoring.

Resource-constrained healthcare scenario: a Type 2 diabetes + heart-failure
patient enrolled in a community remote-monitoring programme running on a
low-cost smartphone; connectivity is intermittent.

Pipeline: sensor_aggregation -> trend_analysis -> alert_generation -> care_plan
  24-hour TTL per daily envelope. Weekly trend consumes seven daily envelopes.
  Model-version upgrade v1.2 -> v1.3 at week 4 captured as PROV Activity.
  `scope` field distinguishes acute threshold crossings from sustained
  trajectory breaches.

Verifiers exercised:
  - verify_temporal_oversight (nurse review after alert)
  - verify_pii_detachment     (patient identifiers tokenised)
  - verify_integrity
  - verify_negative_proof (no raw geolocation in decision chain)

Outputs (usecases/output/):
  chronic_monitoring_envelope.json
  chronic_monitoring_prov.ttl
  chronic_monitoring_audit.json
  chronic_monitoring_metrics.json
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

    # ----- Simulated weekly pipeline snapshot (week 5; upgrade happened at week 4)
    base = datetime(2026, 4, 13, 7, 0, 0, tzinfo=timezone.utc)  # Monday 07:00 UTC
    ts = {
        "sensor_start": base,
        "sensor_end": base + timedelta(minutes=3),         # daily aggregation
        "trend_start": base + timedelta(minutes=5),
        "trend_end": base + timedelta(minutes=8),          # weekly trend
        "alert_start": base + timedelta(minutes=10),
        "alert_end": base + timedelta(minutes=11),
        "care_plan_start": base + timedelta(minutes=13),
        "care_plan_end": base + timedelta(minutes=14),
        # Nurse review (next clinic day morning)
        "nurse_start": base + timedelta(days=1, hours=2),
        "nurse_end": base + timedelta(days=1, hours=2, minutes=7),  # 7-min review
        # Model upgrade one week earlier
        "upgrade_start": base - timedelta(days=7),
        "upgrade_end": base - timedelta(days=7) + timedelta(hours=1),
    }

    prov = PROVGraph("ctx-chronic-monitoring-001")

    # =====================================================================
    # STEP 0: PROV-only record of the v1.2 -> v1.3 trend-model upgrade
    # (no envelope; captured as a PROV Activity so auditors can distinguish
    # apparent trend changes caused by clinical deterioration from
    # model-version effects.)
    # =====================================================================
    prov.add_agent("mlops-team", "MLOps Deployment Team", role="model_operator")
    prov.add_entity("art-trend-model-v1.2", "Trend model v1.2 (retired)",
                    artifact_type="model_version", content_hash="sha256:0001...")
    prov.add_entity("art-trend-model-v1.3", "Trend model v1.3 (active)",
                    artifact_type="model_version", content_hash="sha256:0002...")
    prov.add_activity("model-upgrade-v1.2-v1.3", "Trend model upgrade v1.2 -> v1.3",
                      started_at=_iso(ts["upgrade_start"]),
                      ended_at=_iso(ts["upgrade_end"]),
                      method="rolling OTA deployment")
    prov.was_associated_with("model-upgrade-v1.2-v1.3", "mlops-team")
    prov.was_generated_by("art-trend-model-v1.3", "model-upgrade-v1.2-v1.3")
    prov.was_derived_from("art-trend-model-v1.3", "art-trend-model-v1.2")

    # =====================================================================
    # STEP 1: Sensor-aggregation agent (daily)
    # =====================================================================
    t0 = time.perf_counter()

    sensor_payload = userml_payload(
        observations=[
            observation("patient:P-M318", "hr_mean", 92),
            observation("patient:P-M318", "bp_systolic_mean", 142),
            observation("patient:P-M318", "spo2_min", 91),
            observation("patient:P-M318", "glucose_fasting", 168),
            observation("patient:P-M318", "weight_kg", 81.4),
            observation("patient:P-M318", "steps", 2100),
        ],
    )

    builder = (
        EnvelopeBuilder()
        .set_producer("did:clinic:sensor-aggregation-agent")
        .set_scope("chronic_remote_monitoring")
        .set_ttl("PT24H")  # 24-hour TTL (paper §5.2)
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([sensor_payload])
        .add_artifact(
            artifact_id="art-daily-aggregate",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:aa01b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1",
            model=None,
        )
    )

    prov.add_agent("sensor-agent", "Sensor Aggregation Agent", role="aggregator")
    prov.add_entity("art-daily-aggregate", "Monday daily aggregate (one of seven)",
                    artifact_type="token_sequence", content_hash="sha256:aa01...")
    prov.add_activity("sensor-aggregation", "Daily reading aggregation",
                      started_at=_iso(ts["sensor_start"]),
                      ended_at=_iso(ts["sensor_end"]),
                      method="on-device smartphone")
    prov.was_generated_by("art-daily-aggregate", "sensor-aggregation")
    prov.was_associated_with("sensor-aggregation", "sensor-agent")

    metrics["sensor_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 2: Trend-analysis agent (weekly; uses the upgraded v1.3 model)
    # =====================================================================
    t0 = time.perf_counter()

    trend_payload = userml_payload(
        interpretations=[
            interpretation("patient:P-M318", "trend_bp", "rising",
                           confidence=0.82),
            interpretation("patient:P-M318", "trend_glucose", "rising",
                           confidence=0.79),
        ],
        situations=[
            situation("patient:P-M318", "chronic-decompensation-risk",
                      start=_iso(base - timedelta(days=7)), confidence=0.88),
        ],
    )
    builder.set_semantic_payload([sensor_payload, trend_payload])
    builder.add_artifact(
        artifact_id="art-weekly-trend",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:bb02c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        model="chronic-trend-v1.3",  # upgraded from v1.2 last week
        confidence=0.88,
    )

    prov.add_agent("trend-agent", "Trend Analysis Agent", role="analyser")
    prov.add_entity("art-weekly-trend",
                    "Weekly trend: rising BP + glucose trajectory",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:bb02...")
    prov.add_activity("trend-analysis", "7-day trend inference",
                      started_at=_iso(ts["trend_start"]),
                      ended_at=_iso(ts["trend_end"]),
                      method="LLM inference (chronic-trend-v1.3)")
    prov.used("trend-analysis", "art-daily-aggregate")
    prov.used("trend-analysis", "art-trend-model-v1.3")  # binds model to run
    prov.was_generated_by("art-weekly-trend", "trend-analysis")
    prov.was_associated_with("trend-analysis", "trend-agent")
    prov.was_derived_from("art-weekly-trend", "art-daily-aggregate")

    metrics["trend_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 3: Alert-generation agent
    # Envelope carries a `scope` metadata tag: "sustained_trajectory"
    # distinguishes sustained breaches from acute threshold crossings.
    # =====================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-alert",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:cc03d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        model="alert-rule-engine-v0.9",
        confidence=0.91,
        scope="sustained_trajectory",  # acute | sustained_trajectory
    )
    builder.add_decision_influence(
        agent="alert-agent",
        categories=["trend_bp", "trend_glucose", "patient_situation"],
        influence_weights={
            "trend_bp": 0.4, "trend_glucose": 0.35, "patient_situation": 0.25,
        },
        confidence=0.91,
        abstraction_level=AbstractionLevel.SITUATION,
        temporal_scope=TemporalScope.HISTORICAL,  # weekly window over 7 daily envelopes
    )

    prov.add_agent("alert-agent", "Alert Generation Agent", role="alerter")
    prov.add_entity("art-alert",
                    "Alert: sustained-trajectory breach (nurse review requested)",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:cc03...")
    prov.add_activity("alert-generation", "Threshold/trajectory alert synthesis",
                      started_at=_iso(ts["alert_start"]),
                      ended_at=_iso(ts["alert_end"]),
                      method="rule-based")
    prov.used("alert-generation", "art-weekly-trend")
    prov.was_generated_by("art-alert", "alert-generation")
    prov.was_associated_with("alert-generation", "alert-agent")
    prov.was_derived_from("art-alert", "art-weekly-trend")

    metrics["alert_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 4: Care-plan agent (proposed adjustment)
    # =====================================================================
    t0 = time.perf_counter()

    builder.add_artifact(
        artifact_id="art-care-plan",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:dd04e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        model="care-plan-v0.4",
        confidence=0.8,
    )

    prov.add_agent("care-plan-agent", "Care Plan Agent", role="planner")
    prov.add_entity("art-care-plan",
                    "Proposed: metformin titration + next-day nurse visit",
                    artifact_type="semantic_extraction",
                    content_hash="sha256:dd04...")
    prov.add_activity("care-plan-synthesis", "Proposed care-plan adjustment",
                      started_at=_iso(ts["care_plan_start"]),
                      ended_at=_iso(ts["care_plan_end"]),
                      method="LLM inference (care-plan-v0.4)")
    prov.used("care-plan-synthesis", "art-alert")
    prov.was_generated_by("art-care-plan", "care-plan-synthesis")
    prov.was_associated_with("care-plan-synthesis", "care-plan-agent")
    prov.was_derived_from("art-care-plan", "art-alert")

    builder.set_passed_artifact("art-care-plan")

    metrics["care_plan_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 5: Nurse review (async, next day after opportunistic sync)
    # =====================================================================
    t0 = time.perf_counter()

    prov.add_agent("nurse-amani", "Nurse Amani (community nurse)",
                   role="human_oversight")
    prov.add_activity("nurse-review",
                      "Nurse reviews trend + alert + proposed care plan",
                      started_at=_iso(ts["nurse_start"]),
                      ended_at=_iso(ts["nurse_end"]))
    prov.used("nurse-review", "art-weekly-trend")
    prov.used("nurse-review", "art-alert")
    prov.used("nurse-review", "art-care-plan")
    prov.was_associated_with("nurse-review", "nurse-amani")

    metrics["oversight_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 6: Finalise envelope with PII detachment (identifiers tokenised)
    # =====================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()
    builder.set_privacy(
        data_category="sensitive",  # paper: sensitive_clinical
        legal_basis="informed_consent",
        retention="P1Y",
        storage_policy="edge-encrypted + deferred-sync",
        feature_suppression=["home_address", "raw_geolocation"],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://clinic.example/models/chronic-trend-v1.3",
        escalation_path="primary-care-team@clinic.example",
    )
    builder.enable_pii_detachment(vault=pii_vault)

    prov_digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest
    envelope = builder.sign("did:clinic:compliance-officer").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # STEP 7: Audit
    # =====================================================================
    t0 = time.perf_counter()

    temporal = verify_temporal_oversight(
        prov,
        ai_activity_id="alert-generation",
        human_activities=["nurse-review"],
        min_review_seconds=300.0,
    )
    pii = verify_pii_detachment(envelope)
    integrity = verify_integrity(envelope)
    # Negative proof: home address + raw geolocation never reached the alert
    negative = verify_negative_proof(
        prov,
        decision_entity_id="art-alert",
        excluded_artifact_types=["home_address", "raw_geolocation"],
    )

    report = generate_audit_report(envelope, prov, [temporal, pii, integrity, negative])
    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =====================================================================
    # STEP 8: Save outputs
    # =====================================================================
    env_path = OUTPUT_DIR / "chronic_monitoring_envelope.json"
    env_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    prov_path = OUTPUT_DIR / "chronic_monitoring_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")
    audit_path = OUTPUT_DIR / "chronic_monitoring_audit.json"
    audit_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics["prov_entities"] = len(prov.get_all_entities())
    metrics["prov_activities"] = len(prov.get_temporal_sequence())
    metrics["envelope_size_bytes"] = env_path.stat().st_size
    metrics_path = OUTPUT_DIR / "chronic_monitoring_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # ----- Console summary -----
    print("=" * 64)
    print("Healthcare Scenario 2 - Chronic-Disease Remote Monitoring")
    print("=" * 64)
    print(f"  Context ID:      {envelope.context_id}")
    print(f"  TTL:             {envelope.ttl}  (daily envelope window)")
    print(f"  Risk Level:      {envelope.compliance.risk_level.value}")
    print(f"  Artifacts:       {len(envelope.artifacts_registry)}")
    print(f"  PROV entities:   {metrics['prov_entities']} (incl. model-version upgrade)")
    print(f"  PROV activities: {metrics['prov_activities']}")
    print()
    print("  Audit (Arts. 12 / 14 / 15 + PII):")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall:         {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print(f"  Total runtime:   {metrics['total_ms']:.1f} ms")
    print(f"  Outputs:         {OUTPUT_DIR}/chronic_monitoring_*")
    print("=" * 64)
    return metrics


if __name__ == "__main__":
    run()
