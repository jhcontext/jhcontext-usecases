"""Post-hoc cohort review at the deployer -> regulator corpus boundary.

Builds a corpus of 312 receipts spanning 18 months. Each receipt carries
its own minimal PROV graph; per-receipt verifiers run individually, and
the corpus-level helpers (feature_usage_census + four_fifths_ratio)
operate on the full list. The fixture seeds disparate impact:
``>15y`` advances at 18 % vs ``5-10y`` at 30 %, so the four-fifths ratio
is 0.6 (fails). Two suspension events are recorded; one carries a timely
Art. 73 notification, the other does not, so verify_incident_attestation
flags the second.

Verifiers exercised:
  - verify_negative_proof (per-receipt, sampled)
  - verify_temporal_oversight (per-receipt, sampled)
  - verify_incident_attestation (corpus-level)
  - feature_usage_census (corpus-level)
  - four_fifths_ratio (corpus-level)

Outputs (usecases/output/):
  hiring_cohort_corpus.jsonl    one JSON envelope per line
  hiring_cohort_audit.json      per-receipt verifier results (sampled)
  hiring_cohort_census.json     feature-usage census
  hiring_cohort_four_fifths.json  4/5-rule result
  hiring_cohort_incidents.json  incident-attestation result
  hiring_cohort_metrics.json    timing + counts
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
    verify_negative_proof,
    verify_temporal_oversight,
)

from usecases.hiring import fixtures as fx
from usecases.hiring.cohort import feature_usage_census, four_fifths_ratio
from usecases.hiring.verifiers import verify_incident_attestation

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"

CORPUS_HANDOFF = "hiring_cohort_screening_to_ranking"
SAMPLE_VERIFIER_COUNT = 20  # per-receipt verifiers run only on a sample to keep runtime small


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _inject_flag(argv: list[str]) -> bool:
    if "--inject-violation" in argv:
        return True
    return os.environ.get("HIRING_INJECT_VIOLATION", "").lower() in {"1", "true", "yes"}


def _build_receipt(c: fx.Candidate, idx: int, base: datetime, recruiter_minutes: int):
    """One receipt = one envelope + one tiny PROV graph (per candidate)."""
    receipt_time = base + timedelta(days=idx)
    screening_start = receipt_time
    screening_end = receipt_time + timedelta(minutes=15)
    review_start = screening_end + timedelta(minutes=5)
    review_end = review_start + timedelta(minutes=recruiter_minutes)

    prov = PROVGraph(f"ctx-cohort-{c.candidate_id}")
    prov.add_agent("screening-agent", "Screening Agent", role="ranker")
    prov.add_agent("recruiter-jane", "Jane Doe", role="recruiter")

    prov.add_entity("art-screening-rank", "Per-candidate rank",
                    artifact_type="semantic_extraction")
    prov.add_activity("screening", "Screening",
                      started_at=_iso(screening_start),
                      ended_at=_iso(screening_end))
    prov.was_associated_with("screening", "screening-agent")
    prov.was_generated_by("art-screening-rank", "screening")
    prov.add_activity("recruiter-review", "Recruiter review",
                      started_at=_iso(review_start),
                      ended_at=_iso(review_end))
    prov.was_associated_with("recruiter-review", "recruiter-jane")
    prov.used("recruiter-review", "art-screening-rank")

    builder = (
        EnvelopeBuilder()
        .set_producer(fx.PRODUCERS["screening"])
        .set_scope(CORPUS_HANDOFF)
        .set_ttl("P30D")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([{
            "candidate_id": c.candidate_id,
            "experience_band": c.experience_band,
            "skills_overlap": round(c.skills_overlap, 3),
            "advanced_to_recruiter": c.advanced_to_recruiter,
        }])
        .add_decision_influence(
            agent="screening-agent",
            categories=list(fx.SCREENING_WEIGHTS.keys()),
            influence_weights=dict(fx.SCREENING_WEIGHTS),
            confidence=0.85,
            abstraction_level=AbstractionLevel.SITUATION,
            temporal_scope=TemporalScope.HISTORICAL,
        )
        .add_artifact(
            artifact_id=f"art-rank-{c.candidate_id}",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:" + ("0" * 60) + f"{idx:04d}",
            model="screener-v1.4",
            data_governance_attestation_ref="data-gov:role-fam-swe-2026Q1",
            data_governance_attestation_signer=fx.DPO_SIGNER,
        )
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://vendor.example/models/screener-v1.4",
    )
    builder.set_privacy(
        data_category="sensitive",
        legal_basis="consent",
        retention="P30D",
        feature_suppression=list(fx.SUPPRESSED_IDENTIFIERS),
    )
    builder.sign(fx.COMPLIANCE_SIGNER)
    env = builder.build()
    return env, prov, screening_start, review_start, review_end


def _build_incident_graph(events: list[fx.SuspensionEvent]) -> PROVGraph:
    """Single corpus-level PROV graph for suspension/notification events."""
    prov = PROVGraph("ctx-cohort-incidents")
    for ev in events:
        prov.add_activity(ev.suspension_id, "Model suspension",
                          started_at=_iso(ev.started_at),
                          ended_at=_iso(ev.started_at + timedelta(hours=1)))
        prov.set_entity_attribute(ev.suspension_id, "kind", "suspension")

        if ev.notification_id is not None and ev.notification_offset_days is not None:
            notif_dt = ev.started_at + timedelta(days=ev.notification_offset_days)
            prov.add_activity(ev.notification_id,
                              "Art. 73 notification to authority",
                              started_at=_iso(notif_dt),
                              ended_at=_iso(notif_dt + timedelta(hours=1)))
            prov.set_entity_attribute(ev.notification_id, "kind",
                                      "art73_notification")
            prov.was_informed_by(ev.notification_id, ev.suspension_id)
    return prov


def run(*, inject_violation: bool = False) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {"inject_violation": inject_violation}
    t_total = time.perf_counter()

    # Build the candidate corpus (312 with seeded disparity by default)
    candidates = fx.cohort_candidates()
    base = datetime(2025, 11, 1, 9, 0, 0, tzinfo=timezone.utc)

    t0 = time.perf_counter()
    receipts: list[tuple] = []  # (envelope, prov, screening_dt, review_start, review_end)
    for i, c in enumerate(candidates):
        # Under violation injection, half of the receipts have rubber-stamp
        # reviews (1-minute) so verify_temporal_oversight fails on the sample.
        review_minutes = 1 if (inject_violation and i % 2 == 0) else 6
        receipts.append(_build_receipt(c, i, base, recruiter_minutes=review_minutes))
    metrics["build_corpus_ms"] = (time.perf_counter() - t0) * 1000

    envelopes = [r[0] for r in receipts]

    # =====================================================================
    # Per-receipt verifiers (sampled)
    # =====================================================================
    t0 = time.perf_counter()
    sample_indices = list(range(0, len(receipts), max(1, len(receipts) // SAMPLE_VERIFIER_COUNT)))[:SAMPLE_VERIFIER_COUNT]
    sample_results: list[dict] = []
    for idx in sample_indices:
        env, prov, screening_dt, review_start, review_end = receipts[idx]
        neg = verify_negative_proof(
            prov,
            decision_entity_id="art-screening-rank",
            excluded_artifact_types=["raw_video", "raw_cv_with_identifiers",
                                     "biometric_record", "geolocation_record"],
        )
        temp = verify_temporal_oversight(
            prov,
            ai_activity_id="screening",
            human_activities=["recruiter-review"],
            min_review_seconds=300.0,
        )
        sample_results.append({
            "receipt_index": idx,
            "candidate_id": env.semantic_payload[0]["candidate_id"],
            "negative_proof": {"passed": neg.passed, "message": neg.message},
            "temporal_oversight": {"passed": temp.passed, "message": temp.message},
        })
    metrics["sampled_receipts"] = len(sample_indices)
    metrics["per_receipt_audit_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # Cohort-level helpers
    # =====================================================================
    t0 = time.perf_counter()
    censuses = feature_usage_census(envelopes, handoff_filter=CORPUS_HANDOFF)
    four_fifths = four_fifths_ratio(
        envelopes,
        group_attribute="experience_band",
        protected_value=">15y",
        reference_value="5-10y",
        advancement_predicate=lambda e: bool(
            e.semantic_payload[0].get("advanced_to_recruiter", False),
        ),
    )
    metrics["cohort_helpers_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # Incident attestation (corpus-level)
    # =====================================================================
    t0 = time.perf_counter()
    incidents_graph = _build_incident_graph(fx.suspension_events())
    incidents_result = verify_incident_attestation(incidents_graph)
    metrics["incident_audit_ms"] = (time.perf_counter() - t0) * 1000

    # =====================================================================
    # Persist outputs
    # =====================================================================
    corpus_path = OUTPUT_DIR / "hiring_cohort_corpus.jsonl"
    with corpus_path.open("w", encoding="utf-8") as fh:
        for env in envelopes:
            fh.write(json.dumps(env.to_jsonld(), ensure_ascii=False) + "\n")

    (OUTPUT_DIR / "hiring_cohort_audit.json").write_text(
        json.dumps({"sampled_results": sample_results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "hiring_cohort_census.json").write_text(
        json.dumps([c.to_dict() for c in censuses], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "hiring_cohort_four_fifths.json").write_text(
        json.dumps(four_fifths.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "hiring_cohort_incidents.json").write_text(
        json.dumps({
            "check_name": incidents_result.check_name,
            "passed": incidents_result.passed,
            "evidence": incidents_result.evidence,
            "message": incidents_result.message,
        }, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    metrics["total_ms"] = (time.perf_counter() - t_total) * 1000
    metrics["corpus_size"] = len(envelopes)
    metrics["four_fifths_ratio"] = four_fifths.ratio
    metrics["four_fifths_passed"] = four_fifths.passed
    metrics["incident_attestation_passed"] = incidents_result.passed
    metrics["per_receipt_negative_proof_passed"] = all(
        r["negative_proof"]["passed"] for r in sample_results
    )
    metrics["per_receipt_temporal_oversight_passed"] = all(
        r["temporal_oversight"]["passed"] for r in sample_results
    )

    (OUTPUT_DIR / "hiring_cohort_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8",
    )

    # =====================================================================
    # Console summary
    # =====================================================================
    print("=" * 64)
    print("Hiring Scenario C -- Post-hoc Cohort Review")
    print("=" * 64)
    print(f"  Inject violation: {inject_violation}")
    print(f"  Corpus size:      {len(envelopes)} receipts")
    print(f"  Sampled receipts: {len(sample_indices)}")
    print()
    print("  Per-receipt audit (sampled):")
    print(f"    [{'PASS' if metrics['per_receipt_negative_proof_passed'] else 'FAIL'}]"
          f" negative_proof  ({metrics['sampled_receipts']} receipts)")
    print(f"    [{'PASS' if metrics['per_receipt_temporal_oversight_passed'] else 'FAIL'}]"
          f" temporal_oversight  ({metrics['sampled_receipts']} receipts)")
    print()
    print("  Cohort-level findings:")
    if censuses:
        c = censuses[0]
        print(f"    feature_usage_census ({c.handoff}): {c.total_receipts} receipts; "
              f"top features: {list(c.feature_counts.items())[:4]}")
    print(f"    four_fifths_ratio: protected={four_fifths.selection_rate_protected:.3f} "
          f"reference={four_fifths.selection_rate_reference:.3f} "
          f"ratio={four_fifths.ratio:.3f}  "
          f"[{'PASS' if four_fifths.passed else 'FAIL'}]")
    status = "PASS" if incidents_result.passed else "FAIL"
    print(f"    [{status}] incident_attestation: {incidents_result.message}")
    print()
    print(f"  Total runtime:    {metrics['total_ms']:.1f} ms")
    print(f"  Outputs:          {OUTPUT_DIR}/hiring_cohort_*")
    print("=" * 64)
    return metrics


if __name__ == "__main__":
    run(inject_violation=_inject_flag(sys.argv[1:]))
