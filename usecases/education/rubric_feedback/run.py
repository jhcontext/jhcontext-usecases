"""Rubric-Grounded LLM Feedback.

Auditable AI assessment scenario — provenance-aware evaluation and
rubric-grounded feedback for student essay submissions.

Pipeline: essay submission -> LLM feedback agent -> per-sentence envelope ->
rubric-grounding audit.

For a single submission we produce one envelope per feedback sentence
(typically 6-10), each binding the sentence to a specific rubric criterion,
the textual evidence span it cites (offset + length + hash), the model
version, and the prompt-template hash. A dedicated verifier resolves each
sentence's cited evidence span against the submission hash and flags
non-existent or mis-bound spans.

Classroom-scale benchmark (below) measures the aggregate envelope-construction
cost for 500 submissions × 8 feedback sentences = 4,000 per-sentence envelopes.

Verifiers exercised:
  - verify_rubric_grounding    (every feedback sentence bound to criterion + span)
  - verify_integrity           (envelope signatures + content hashes)

Outputs (usecases/output/):
  rubric_feedback_envelopes.jsonl   One envelope per feedback sentence
  rubric_feedback_prov.ttl          W3C PROV graph (grading chain)
  rubric_feedback_audit.json        Audit report
  rubric_feedback_metrics.json      Timing + benchmark metrics
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
    verify_rubric_grounding,
    generate_audit_report,
)
from jhcontext.pii import InMemoryPIIVault
from jhcontext.crypto import compute_sha256

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# -- Rubric + submission fixtures ---------------------------------------------

RUBRIC_ID = "rubric_v2.3"
RUBRIC_CRITERIA = [
    "C1-thesis_clarity",
    "C2-argument_quality",
    "C3-evidence_integration",
    "C4-writing_mechanics",
]
MODEL_ID = "gpt-4o-2024-08-06"
PROMPT_TEMPLATE_ID = "fb_per_criterion_v4"

# Simulated essay (single submission for the full-detail run)
ESSAY_TEXT = (
    "Climate policy in the twenty-first century must reconcile economic growth "
    "with ecological limits. Carbon pricing, though politically difficult, has "
    "proved effective in jurisdictions that have adopted it. Recent data from "
    "British Columbia shows that a revenue-neutral carbon tax reduced emissions "
    "by 9% between 2008 and 2012 without dampening provincial GDP growth. "
    "However, critics note that equity effects have been mixed, with low-income "
    "households bearing a disproportionate share of the cost in the programme's "
    "first two years. A well-designed rebate mechanism can mitigate this."
)


def _submission_hash(text: str) -> str:
    return "sha256:" + compute_sha256(text.encode("utf-8"))[:16] + "..."


def _feedback_sentences() -> list[dict]:
    """Return 8 LLM-generated feedback sentences for the essay.

    Seven are rubric-grounded (each cites a real span in ESSAY_TEXT); one is
    an orphan that claims to assess a criterion without a valid evidence span.
    The verifier flags the orphan.
    """
    spans = [
        # (criterion, offset, length, cited_text_preview, is_grounded)
        ("C1-thesis_clarity",     0,    93,  "Climate policy ... ecological limits.",           True),
        ("C2-argument_quality",   94,   84,  "Carbon pricing ... adopted it.",                  True),
        ("C3-evidence_integration", 178, 127, "Recent data ... 2012 ... GDP growth.",          True),
        ("C4-writing_mechanics",  305,  59,  "However, critics note ... equity effects.",       True),
        ("C3-evidence_integration", 364, 94, "with low-income ... two years.",                 True),
        ("C2-argument_quality",   458,  51,  "A well-designed rebate mechanism can mitigate.",  True),
        ("C1-thesis_clarity",     0,    93,  "Climate policy ... ecological limits.",           True),
        # 8th sentence: orphan — claims C4 but cites no valid span
        ("C4-writing_mechanics",  None, None, None,                                              False),
    ]
    sentences = []
    for i, (crit, off, length, preview, grounded) in enumerate(spans, start=1):
        fs = {
            "id": f"art-fb-sentence-{i:02d}",
            "criterion_id": f"{RUBRIC_ID}#{crit}",
            "text_hash": "sha256:" + compute_sha256(f"fb-{i}".encode("utf-8"))[:16] + "...",
            "model_id": MODEL_ID,
            "prompt_template_id": PROMPT_TEMPLATE_ID,
            "prompt_template_hash": "sha256:" + compute_sha256(PROMPT_TEMPLATE_ID.encode("utf-8"))[:16] + "...",
            "grounded": grounded,
        }
        if grounded:
            cited = ESSAY_TEXT[off:off + length]
            fs["evidence_span_offset"] = off
            fs["evidence_span_length"] = length
            fs["evidence_span_hash"] = "sha256:" + compute_sha256(cited.encode("utf-8"))[:16] + "..."
            fs["cited_preview"] = preview
        sentences.append(fs)
    return sentences


# -- Scenario: one submission with full-detail envelopes ----------------------

def _build_scenario_envelopes(
    submission_id: str,
    essay_text: str,
    sentences: list[dict],
    ts_base: datetime,
    prov: PROVGraph | None = None,
    emit_envelopes: bool = True,
):
    """Build one envelope per feedback sentence. Return (envelopes, prov_graph)."""
    submission_hash = _submission_hash(essay_text)
    submission_entity = f"art-submission-{submission_id}"

    # PROV graph for the grading chain
    if prov is None:
        prov = PROVGraph(f"ctx-edu-feedback-{submission_id}")

    prov.add_entity(submission_entity, f"Student submission {submission_id}",
                    artifact_type="token_sequence", content_hash=submission_hash)
    prov.add_entity(f"art-rubric-{RUBRIC_ID}", f"Rubric {RUBRIC_ID}",
                    artifact_type="token_sequence",
                    content_hash="sha256:" + compute_sha256(RUBRIC_ID.encode("utf-8"))[:16] + "...")
    prov.add_agent("feedback-agent", "LLM Feedback Agent", role="feedback_generator")
    prov.add_activity(
        f"feedback-generation-{submission_id}",
        "Per-criterion feedback generation",
        started_at=_iso(ts_base),
        ended_at=_iso(ts_base + timedelta(seconds=5)),
        method=f"LLM inference ({MODEL_ID})",
    )
    prov.used(f"feedback-generation-{submission_id}", submission_entity)
    prov.used(f"feedback-generation-{submission_id}", f"art-rubric-{RUBRIC_ID}")
    prov.was_associated_with(f"feedback-generation-{submission_id}", "feedback-agent")

    envelopes = []
    for fs in sentences:
        # Each feedback sentence is its own artifact/entity in the grading PROV graph
        prov.add_entity(
            fs["id"],
            f"Feedback sentence assessing {fs['criterion_id']}",
            artifact_type="semantic_extraction",
            content_hash=fs["text_hash"],
        )
        prov.was_generated_by(fs["id"], f"feedback-generation-{submission_id}")
        prov.was_derived_from(fs["id"], submission_entity)
        prov.was_derived_from(fs["id"], f"art-rubric-{RUBRIC_ID}")
        # Attach the domain-specific binding attributes
        prov.set_entity_attribute(fs["id"], "rubricCriterionId", fs["criterion_id"])
        prov.set_entity_attribute(fs["id"], "modelVersion", fs["model_id"])
        prov.set_entity_attribute(fs["id"], "promptTemplateHash", fs["prompt_template_hash"])
        if fs["grounded"]:
            prov.set_entity_attribute(fs["id"], "evidenceSpanOffset", fs["evidence_span_offset"])
            prov.set_entity_attribute(fs["id"], "evidenceSpanLength", fs["evidence_span_length"])
            prov.set_entity_attribute(fs["id"], "evidenceSpanHash", fs["evidence_span_hash"])

        if not emit_envelopes:
            continue

        # Build the per-sentence envelope (paper §3 Figure 1 layout)
        builder = (
            EnvelopeBuilder()
            .set_producer("did:university:feedback-agent-v1.2")
            .set_scope("education_assessment")
            .set_ttl("P5Y")  # Art. 12 retention
            .set_risk_level(RiskLevel.HIGH)
            .set_human_oversight(True)
            .set_semantic_payload([
                userml_payload(
                    observations=[
                        observation(submission_entity, "rubric_criterion_id", fs["criterion_id"]),
                        observation(submission_entity, "model_version", fs["model_id"]),
                        observation(submission_entity, "prompt_template_id", fs["prompt_template_id"]),
                    ],
                    interpretations=(
                        [interpretation(submission_entity, "evidence_span_hash",
                                        fs["evidence_span_hash"], confidence=1.0)]
                        if fs["grounded"] else []
                    ),
                )
            ])
            .add_artifact(
                artifact_id=fs["id"],
                artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
                content_hash=fs["text_hash"],
                model=fs["model_id"],
            )
            .add_artifact(
                artifact_id=submission_entity,
                artifact_type=ArtifactType.TOKEN_SEQUENCE,
                content_hash=submission_hash,
            )
            .add_artifact(
                artifact_id=f"art-rubric-{RUBRIC_ID}",
                artifact_type=ArtifactType.TOKEN_SEQUENCE,
                content_hash="sha256:" + compute_sha256(RUBRIC_ID.encode("utf-8"))[:16] + "...",
            )
            .set_passed_artifact(fs["id"])
            .set_privacy(
                data_category="behavioral",
                legal_basis="legitimate_interest",
                retention="P5Y",
                storage_policy="university-encrypted",
                feature_suppression=["student_name", "student_id", "accommodation_flags"],
            )
            .set_compliance(
                risk_level=RiskLevel.HIGH,
                human_oversight_required=True,
                model_card_ref="https://university.example/models/feedback-agent-v1.2",
                escalation_path="academic-affairs@university.example",
            )
        )
        envelope = builder.sign("did:university:compliance-officer").build()
        envelopes.append(envelope)

    return envelopes, prov


# -- Classroom-scale benchmark ------------------------------------------------

def _benchmark_envelope_construction(
    n_submissions: int = 500,
    sentences_per_submission: int = 8,
) -> dict:
    """Construct n_submissions × sentences_per_submission envelopes and time it.

    Paper §5 claim: under 16 s aggregate for 500 × 8 = 4,000 envelopes.
    """
    ts_base = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    total_envelopes = 0
    t0 = time.perf_counter()
    for s_idx in range(n_submissions):
        sub_id = f"S-{s_idx:05d}"
        sentences = _feedback_sentences()[:sentences_per_submission]
        envelopes, _ = _build_scenario_envelopes(
            submission_id=sub_id,
            essay_text=ESSAY_TEXT,
            sentences=sentences,
            ts_base=ts_base + timedelta(seconds=s_idx),
        )
        total_envelopes += len(envelopes)
    elapsed_s = time.perf_counter() - t0
    return {
        "n_submissions": n_submissions,
        "sentences_per_submission": sentences_per_submission,
        "total_envelopes": total_envelopes,
        "elapsed_seconds": elapsed_s,
        "per_envelope_ms": (elapsed_s * 1000) / max(1, total_envelopes),
    }


# -- Main ---------------------------------------------------------------------

def run() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    # 1. Full-detail run for ONE submission (all 8 feedback sentences)
    ts_base = datetime(2026, 5, 14, 10, 32, 17, tzinfo=timezone.utc)
    sentences = _feedback_sentences()
    envelopes, prov = _build_scenario_envelopes(
        submission_id="S-98765",
        essay_text=ESSAY_TEXT,
        sentences=sentences,
        ts_base=ts_base,
    )

    # 2. Audit
    t0 = time.perf_counter()
    rubric_check = verify_rubric_grounding(
        prov,
        feedback_sentence_ids=[s["id"] for s in sentences],
        submission_entity_id="art-submission-S-98765",
    )
    integrity_checks = [verify_integrity(e) for e in envelopes]
    integrity_passed = all(c.passed for c in integrity_checks)
    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000

    # 3. Classroom-scale benchmark (500 × 8 = 4000 per-sentence envelopes)
    bench = _benchmark_envelope_construction(n_submissions=500, sentences_per_submission=8)
    metrics["benchmark"] = bench

    # 4. Persist outputs
    # Envelopes (JSONL — one per line)
    env_path = OUTPUT_DIR / "rubric_feedback_envelopes.jsonl"
    with env_path.open("w", encoding="utf-8") as f:
        for e in envelopes:
            f.write(json.dumps(e.to_jsonld(), ensure_ascii=False) + "\n")
    metrics["envelopes_emitted"] = len(envelopes)

    # PROV graph
    prov_path = OUTPUT_DIR / "rubric_feedback_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")

    # Audit report
    audit_report = {
        "context_id": f"ctx-edu-feedback-S-98765",
        "rubric_grounding": {
            "passed": rubric_check.passed,
            "message": rubric_check.message,
            "evidence": rubric_check.evidence,
        },
        "integrity": {
            "passed": integrity_passed,
            "envelopes_checked": len(integrity_checks),
        },
    }
    audit_path = OUTPUT_DIR / "rubric_feedback_audit.json"
    audit_path.write_text(json.dumps(audit_report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Metrics
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000
    metrics_path = OUTPUT_DIR / "rubric_feedback_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # 5. Print summary
    print("=" * 68)
    print("SCENARIO B — Rubric-Grounded LLM Feedback (§4.2)")
    print("=" * 68)
    print(f"  Submission:          S-98765")
    print(f"  Rubric:              {RUBRIC_ID} ({len(RUBRIC_CRITERIA)} criteria)")
    print(f"  Model:               {MODEL_ID}")
    print(f"  Feedback sentences:  {len(sentences)}")
    print(f"  Per-sentence envelopes emitted: {len(envelopes)}")
    print()
    print("  AUDIT RESULTS:")
    status = "PASS" if rubric_check.passed else "FAIL"
    print(f"    [{status}] rubric_grounding: {rubric_check.message}")
    print(f"    [{'PASS' if integrity_passed else 'FAIL'}] integrity: "
          f"{len(integrity_checks)} envelopes verified")
    if rubric_check.evidence.get("orphans"):
        print("    Orphan sentences flagged:")
        for o in rubric_check.evidence["orphans"]:
            print(f"      - {o['feedback_sentence']}: {', '.join(o['issues'])}")
    print()
    print(f"  CLASSROOM-SCALE BENCHMARK ({bench['n_submissions']} submissions × "
          f"{bench['sentences_per_submission']} sentences):")
    print(f"    Total envelopes:    {bench['total_envelopes']:,}")
    print(f"    Aggregate time:     {bench['elapsed_seconds']:.2f} s")
    print(f"    Per-envelope cost:  {bench['per_envelope_ms']:.2f} ms")
    print()
    print(f"  Outputs saved to: {OUTPUT_DIR}/")
    print("=" * 68)
    return metrics


if __name__ == "__main__":
    run()
