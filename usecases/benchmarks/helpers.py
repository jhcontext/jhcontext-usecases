"""Shared utilities for benchmarks: timing harness, envelope/graph factories."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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
)
from jhcontext.models import Envelope
from jhcontext.server.storage.sqlite import SQLiteStorage


def timed(fn: Callable[[], Any], iterations: int, warmup: int) -> dict:
    """Run fn `warmup + iterations` times, return stats over the last `iterations`."""
    times = []
    for i in range(warmup + iterations):
        t0 = time.perf_counter()
        result = fn()
        elapsed = (time.perf_counter() - t0) * 1000  # ms
        if i >= warmup:
            times.append(elapsed)
    times.sort()
    return {
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "std": statistics.stdev(times) if len(times) > 1 else 0.0,
        "min": times[0],
        "max": times[-1],
        "p95": times[int(len(times) * 0.95)],
        "p99": times[int(len(times) * 0.99)],
        "n": len(times),
    }


def fresh_storage(tmp_dir: str | None = None) -> SQLiteStorage:
    """Create a SQLiteStorage backed by a fresh temp directory."""
    d = tmp_dir or tempfile.mkdtemp(prefix="jhctx_bench_")
    return SQLiteStorage(
        db_path=str(Path(d) / "bench.db"),
        artifacts_dir=str(Path(d) / "artifacts"),
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def build_healthcare_envelope() -> tuple[Envelope, PROVGraph]:
    """Build the healthcare scenario envelope + PROV graph (no I/O)."""
    base = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
    ts = {
        "sensor_start": base,
        "sensor_end": base + timedelta(minutes=2),
        "situation_start": base + timedelta(minutes=3),
        "situation_end": base + timedelta(minutes=5),
        "decision_start": base + timedelta(minutes=6),
        "decision_end": base + timedelta(minutes=7),
        "oversight_start": base + timedelta(minutes=10),
        "ct_review_end": base + timedelta(minutes=14),
        "history_review_end": base + timedelta(minutes=17),
        "pathology_review_end": base + timedelta(minutes=19),
        "ai_review_end": base + timedelta(minutes=20),
        "override_time": base + timedelta(minutes=20, seconds=30),
    }

    payload = userml_payload(
        observations=[
            observation("patient:P-12345", "age", 62),
            observation("patient:P-12345", "tumor_marker_CEA", 8.7),
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
        .set_semantic_payload([payload])
        .add_artifact("art-demographics", ArtifactType.TOKEN_SEQUENCE, "sha256:a1b2c3d4" * 8)
        .add_artifact("art-lab-results", ArtifactType.TOKEN_SEQUENCE, "sha256:b2c3d4e5" * 8)
        .add_artifact("art-ct-scan-meta", ArtifactType.TOKEN_SEQUENCE, "sha256:c3d4e5f6" * 8)
        .add_artifact("art-semantic-extraction", ArtifactType.SEMANTIC_EXTRACTION, "sha256:d4e5f6a1" * 8, model="clinical-situation-v2", confidence=0.93)
        .set_passed_artifact("art-semantic-extraction")
        .add_artifact("art-recommendation", ArtifactType.SEMANTIC_EXTRACTION, "sha256:e5f6a1b2" * 8, model="oncology-decision-v3", confidence=0.87)
        .add_artifact("art-final-decision", ArtifactType.SEMANTIC_EXTRACTION, "sha256:f6a1b2c3" * 8, confidence=1.0)
        .add_decision_influence(
            agent="oncology-decision-agent",
            categories=["patient_status", "tumor_response"],
            influence_weights={"patient_status": 0.95, "tumor_response": 0.80},
            confidence=0.87,
            abstraction_level=AbstractionLevel.SITUATION,
            temporal_scope=TemporalScope.CURRENT,
        )
        .set_privacy(data_category="sensitive", legal_basis="vital_interest", retention="P2Y")
        .set_compliance(risk_level=RiskLevel.HIGH, human_oversight_required=True)
    )

    # PROV
    prov = PROVGraph("ctx-health-bench")
    prov.add_agent("sensor-agent", "Sensor Agent", role="data_collector")
    prov.add_agent("situation-agent", "Situation Agent", role="interpreter")
    prov.add_agent("decision-agent", "Decision Agent", role="decision_maker")
    prov.add_agent("dr-chen", "Dr. Chen", role="human_oversight")

    prov.add_entity("art-demographics", "Demographics", artifact_type="token_sequence")
    prov.add_entity("art-lab-results", "Lab Results", artifact_type="token_sequence")
    prov.add_entity("art-ct-scan-meta", "CT Scan", artifact_type="token_sequence")
    prov.add_entity("art-semantic-extraction", "Situation", artifact_type="semantic_extraction")
    prov.add_entity("art-recommendation", "AI Recommendation", artifact_type="semantic_extraction")
    prov.add_entity("art-final-decision", "Final Decision", artifact_type="semantic_extraction")

    prov.add_activity("sensor-collection", "Data Collection", started_at=_iso(ts["sensor_start"]), ended_at=_iso(ts["sensor_end"]))
    prov.add_activity("situation-recognition", "Situation Analysis", started_at=_iso(ts["situation_start"]), ended_at=_iso(ts["situation_end"]))
    prov.add_activity("ai-recommendation", "AI Recommendation", started_at=_iso(ts["decision_start"]), ended_at=_iso(ts["decision_end"]))
    prov.add_activity("review-ct-scan", "Review CT Scan", started_at=_iso(ts["oversight_start"]), ended_at=_iso(ts["ct_review_end"]))
    prov.add_activity("review-history", "Review History", started_at=_iso(ts["ct_review_end"]), ended_at=_iso(ts["history_review_end"]))
    prov.add_activity("review-pathology", "Review Pathology", started_at=_iso(ts["history_review_end"]), ended_at=_iso(ts["pathology_review_end"]))
    prov.add_activity("review-ai-recommendation", "Review AI Rec", started_at=_iso(ts["pathology_review_end"]), ended_at=_iso(ts["ai_review_end"]))
    prov.add_activity("physician-override", "Physician Override", started_at=_iso(ts["ai_review_end"]), ended_at=_iso(ts["override_time"]))

    prov.was_generated_by("art-demographics", "sensor-collection")
    prov.was_generated_by("art-lab-results", "sensor-collection")
    prov.was_generated_by("art-ct-scan-meta", "sensor-collection")
    prov.was_associated_with("sensor-collection", "sensor-agent")

    prov.used("situation-recognition", "art-demographics")
    prov.used("situation-recognition", "art-lab-results")
    prov.used("situation-recognition", "art-ct-scan-meta")
    prov.was_generated_by("art-semantic-extraction", "situation-recognition")
    prov.was_associated_with("situation-recognition", "situation-agent")
    prov.was_derived_from("art-semantic-extraction", "art-demographics")
    prov.was_derived_from("art-semantic-extraction", "art-lab-results")

    prov.used("ai-recommendation", "art-semantic-extraction")
    prov.was_generated_by("art-recommendation", "ai-recommendation")
    prov.was_associated_with("ai-recommendation", "decision-agent")
    prov.was_derived_from("art-recommendation", "art-semantic-extraction")

    prov.used("review-ct-scan", "art-ct-scan-meta")
    prov.was_associated_with("review-ct-scan", "dr-chen")
    prov.used("review-history", "art-demographics")
    prov.was_associated_with("review-history", "dr-chen")
    prov.used("review-pathology", "art-lab-results")
    prov.was_associated_with("review-pathology", "dr-chen")
    prov.used("review-ai-recommendation", "art-recommendation")
    prov.was_associated_with("review-ai-recommendation", "dr-chen")

    prov.was_generated_by("art-final-decision", "physician-override")
    prov.was_associated_with("physician-override", "dr-chen")
    prov.was_derived_from("art-final-decision", "art-recommendation")

    # Attach prov ref before signing
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov.digest()

    envelope = builder.sign("did:hospital:compliance-officer").build()
    return envelope, prov


def build_education_envelope() -> tuple[Envelope, PROVGraph, PROVGraph]:
    """Build the education scenario envelope + grading PROV + equity PROV (no I/O)."""
    base = datetime(2026, 3, 15, 14, 0, 0, tzinfo=timezone.utc)

    builder = (
        EnvelopeBuilder()
        .set_producer("did:university:ingestion-agent")
        .set_scope("education_assessment")
        .set_ttl("P30D")
        .set_risk_level(RiskLevel.HIGH)
        .set_semantic_payload([
            userml_payload(observations=[
                observation("submission:S-98765", "essay_word_count", 1527),
                observation("submission:S-98765", "essay_topic", "climate_policy"),
            ]),
        ])
        .add_artifact("art-essay-text", ArtifactType.TOKEN_SEQUENCE, "sha256:1111aaaa" * 8)
        .add_artifact("art-rubric", ArtifactType.TOKEN_SEQUENCE, "sha256:2222bbbb" * 8)
        .add_artifact("art-grade-result", ArtifactType.SEMANTIC_EXTRACTION, "sha256:3333cccc" * 8, model="essay-grader-v2", confidence=0.91)
        .set_passed_artifact("art-grade-result")
        .add_decision_influence(
            agent="essay-grading-agent",
            categories=["argument_quality", "evidence_use", "writing_clarity", "critical_thinking"],
            influence_weights={"argument_quality": 0.30, "evidence_use": 0.30, "writing_clarity": 0.20, "critical_thinking": 0.20},
            confidence=0.91,
            abstraction_level=AbstractionLevel.INTERPRETATION,
            temporal_scope=TemporalScope.CURRENT,
        )
        .set_privacy(data_category="behavioral", legal_basis="legitimate_interest", retention="P1Y",
                     feature_suppression=["student_name", "student_id"])
        .set_compliance(risk_level=RiskLevel.HIGH, human_oversight_required=False)
    )

    # Grading PROV
    prov_grading = PROVGraph("ctx-edu-grading-bench")
    prov_grading.add_agent("ingestion-agent", "Ingestion Agent", role="data_processor")
    prov_grading.add_agent("grading-agent", "Grading Agent", role="evaluator")
    prov_grading.add_entity("art-essay-text", "Essay Text", artifact_type="token_sequence")
    prov_grading.add_entity("art-rubric", "Rubric", artifact_type="token_sequence")
    prov_grading.add_entity("art-grade-result", "Grade Result", artifact_type="semantic_extraction")
    prov_grading.add_activity("essay-ingestion", "Essay Ingestion",
                              started_at=_iso(base), ended_at=_iso(base + timedelta(seconds=30)))
    prov_grading.add_activity("ai-grading", "AI Grading",
                              started_at=_iso(base + timedelta(minutes=1)),
                              ended_at=_iso(base + timedelta(minutes=3)))
    prov_grading.was_generated_by("art-essay-text", "essay-ingestion")
    prov_grading.was_associated_with("essay-ingestion", "ingestion-agent")
    prov_grading.used("ai-grading", "art-essay-text")
    prov_grading.used("ai-grading", "art-rubric")
    prov_grading.was_generated_by("art-grade-result", "ai-grading")
    prov_grading.was_associated_with("ai-grading", "grading-agent")
    prov_grading.was_derived_from("art-grade-result", "art-essay-text")
    prov_grading.was_derived_from("art-grade-result", "art-rubric")

    # Equity PROV (isolated)
    prov_equity = PROVGraph("ctx-edu-equity-bench")
    prov_equity.add_agent("equity-agent", "Equity Agent", role="compliance_reporter")
    prov_equity.add_entity("art-student-identity", "Student Identity", artifact_type="biometric")
    prov_equity.add_entity("art-demographic-attrs", "Demographics", artifact_type="sensitive")
    prov_equity.add_entity("art-equity-report", "Equity Report", artifact_type="semantic_extraction")
    prov_equity.add_activity("equity-reporting", "Equity Statistics",
                             started_at=_iso(base + timedelta(minutes=5)),
                             ended_at=_iso(base + timedelta(minutes=6)))
    prov_equity.used("equity-reporting", "art-student-identity")
    prov_equity.used("equity-reporting", "art-demographic-attrs")
    prov_equity.was_generated_by("art-equity-report", "equity-reporting")
    prov_equity.was_associated_with("equity-reporting", "equity-agent")

    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov_grading.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_grading.digest()

    envelope = builder.sign("did:university:compliance-officer").build()
    return envelope, prov_grading, prov_equity


def generate_prov_graph(n_entities: int) -> PROVGraph:
    """Generate a synthetic pipeline-shaped PROV graph with ~n_entities entities.

    Creates multi-stage pipelines: each stage has 1 agent, 1 activity,
    2-3 input entities, 1 output entity, with wasDerivedFrom and
    wasInformedBy links.
    """
    prov = PROVGraph(f"ctx-scale-{n_entities}")
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    entities_created = 0
    stage = 0
    prev_output = None
    prev_activity = None

    # Create initial source entities
    n_sources = min(3, max(1, n_entities // 5))
    for i in range(n_sources):
        eid = f"source-{i}"
        prov.add_entity(eid, f"Source Data {i}", artifact_type="token_sequence",
                        content_hash=f"sha256:{i:064x}")
        entities_created += 1

    while entities_created < n_entities:
        stage += 1
        agent_id = f"agent-{stage}"
        activity_id = f"activity-{stage}"
        output_id = f"entity-{entities_created}"

        prov.add_agent(agent_id, f"Agent {stage}", role="processor")

        start = base + timedelta(minutes=stage * 5)
        end = start + timedelta(minutes=3)
        prov.add_activity(activity_id, f"Processing Stage {stage}",
                          started_at=_iso(start), ended_at=_iso(end),
                          method=f"method-{stage}")

        prov.add_entity(output_id, f"Output {entities_created}",
                        artifact_type="semantic_extraction",
                        content_hash=f"sha256:{entities_created:064x}")
        entities_created += 1

        # Link to previous output or sources
        if prev_output:
            prov.used(activity_id, prev_output)
            prov.was_derived_from(output_id, prev_output)
        else:
            for i in range(n_sources):
                prov.used(activity_id, f"source-{i}")
                prov.was_derived_from(output_id, f"source-{i}")

        prov.was_generated_by(output_id, activity_id)
        prov.was_associated_with(activity_id, agent_id)

        if prev_activity:
            prov.was_informed_by(activity_id, prev_activity)

        prev_output = output_id
        prev_activity = activity_id

    return prov
