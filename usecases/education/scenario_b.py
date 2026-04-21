"""Education Scenario B — Rubric-Grounded LLM Feedback (AIET).

Builds a feedback-generation envelope whose semantic_payload carries, per
feedback sentence, one Interpretation-group SituationalStatement binding
the sentence to a rubric criterion + evidence span, plus one Application-
group statement with the sentence text. This matches the envelope shown
in Figure 1 of the AIET paper.

Outputs:
  - output/education_scenario_b_envelope.json
  - output/education_scenario_b_prov.ttl
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from jhcontext import (
    ArtifactType,
    EnvelopeBuilder,
    PROVGraph,
    RiskLevel,
    interpretation,
    observation,
    application,
    situation,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


def _feedback_sentences() -> list[dict]:
    """Synthetic per-sentence fixture: each row = (sentence id, criterion, span).

    Eight sentences mirror the AIET paper's benchmark fixture.
    """
    return [
        {"id": "fb-9a1b", "criterion": "C3-evidence_integration",
         "text": "Your argument relies on...", "offset": 1247, "length": 189,
         "hash": "sha256:7c4a...", "confidence": 0.82},
        {"id": "fb-2e7c", "criterion": "C1-thesis_clarity",
         "text": "Your thesis is clearly stated...", "offset": 12, "length": 93,
         "hash": "sha256:b1a2...", "confidence": 0.88},
        {"id": "fb-4f21", "criterion": "C2-coherence",
         "text": "The transition between paragraphs...", "offset": 512, "length": 78,
         "hash": "sha256:3e91...", "confidence": 0.75},
        {"id": "fb-8d03", "criterion": "C3-evidence_integration",
         "text": "The cited study supports...", "offset": 2104, "length": 142,
         "hash": "sha256:9a2f...", "confidence": 0.91},
        {"id": "fb-6b17", "criterion": "C4-counterargument",
         "text": "A counter-example would strengthen...", "offset": 2890, "length": 167,
         "hash": "sha256:4d11...", "confidence": 0.68},  # intentionally low-conf
        {"id": "fb-1c55", "criterion": "C5-writing_mechanics",
         "text": "Consider revising the passive voice in...", "offset": 755, "length": 54,
         "hash": "sha256:a8c7...", "confidence": 0.94},
        {"id": "fb-3e88", "criterion": "C1-thesis_clarity",
         "text": "Your concluding paragraph restates...", "offset": 3410, "length": 98,
         "hash": "sha256:bc44...", "confidence": 0.79},
        {"id": "fb-5a42", "criterion": "C3-evidence_integration",
         "text": "The statistical evidence is compelling...", "offset": 1820, "length": 206,
         "hash": "sha256:77ee...", "confidence": 0.59},  # also low-conf, demo orphan
    ]


def run() -> dict:
    """Build and persist the Scenario B feedback-generation envelope."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    created_at = datetime(2026, 5, 14, 10, 32, 17, tzinfo=timezone.utc).isoformat()

    # Build the SituationReport.
    payload: list[dict] = []

    # Envelope-wide observations about the submission.
    payload.append(observation(
        "submission:S-98765", "rubric_version", "rubric_v2.3",
        range_="RubricVersionURI",
        source="submission_metadata",
    ))
    payload.append(observation(
        "submission:S-98765", "word_count", 527,
        range_="non-negative-integer",
        source="submission_metadata",
    ))

    # Per-feedback-sentence: Interpretation (criterion binding + span) +
    # Application (sentence text).
    for fb in _feedback_sentences():
        interp = interpretation(
            f"feedback:{fb['id']}",
            "rubric_criterion",
            f"rubric_v2.3#{fb['criterion']}",
            range_="RubricCriterionURI",
            confidence=fb["confidence"],
            creator="did:example:feedback-agent",
            method="prompt_template:fb_per_criterion_v4",
        )
        # Override the default 'hasAssessment' with the paper's 'addresses'.
        interp["mainpart"]["auxiliary"] = "addresses"
        # Attach the evidence span to the explanation box.
        interp["explanation"]["evidence"] = {
            "offset": fb["offset"],
            "length": fb["length"],
            "hash": fb["hash"],
        }
        payload.append(interp)

        app = application(
            f"feedback:{fb['id']}", "feedback_sentence", fb["text"],
            range_="string", auxiliary="hasText",
        )
        payload.append(app)

    # Aggregated-state statement: summative-assessment context.
    payload.append(situation(
        "submission:S-98765", "summative_assessment",
        range_="formative|summative|draft",
        confidence=0.95,
    ))
    # Rename predicate from default 'activity' to the paper's 'assessment_mode'.
    payload[-1]["mainpart"]["predicate"] = "assessment_mode"

    # Build envelope.
    builder = (
        EnvelopeBuilder()
        .set_producer("did:university:feedback-agent")
        .set_scope("education_assessment_feedback")
        .set_ttl("P5Y")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(False)
        .set_semantic_payload(payload)
        .add_artifact(
            artifact_id="rubric_v2.3",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
        )
        .add_artifact(
            artifact_id="essay_2026_S1_0042",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2",
        )
        .add_artifact(
            artifact_id="fb_per_criterion_v4",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3",
        )
        .add_artifact(
            artifact_id="gpt-4o-2024-08-06",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4",
            model="gpt-4o-2024-08-06",
            confidence=None,
        )
        .set_passed_artifact("essay_2026_S1_0042")
    )
    builder.set_privacy(
        data_category="behavioral",
        legal_basis="gdpr_art_6_1_e_public_interest",
        retention="P5Y",
        storage_policy="university-encrypted",
        feature_suppression=["student_name", "student_id", "accommodation_flags"],
    )
    builder.set_compliance(risk_level=RiskLevel.HIGH, human_oversight_required=False)

    # PROV graph — one entity per feedback sentence.
    prov = PROVGraph(f"ctx-edu-scenario-b-001")
    prov.add_agent("feedback-agent", "LLM Feedback Generator", role="ai_author")
    prov.add_entity("submission_artifact", "Submission S-98765",
                    artifact_type="token_sequence",
                    content_hash="sha256:b2b2...")
    prov.add_entity("rubric_artifact", "Rubric v2.3",
                    artifact_type="token_sequence",
                    content_hash="sha256:a1a1...")
    prov.add_activity(
        "feedback-generation", "Per-criterion feedback generation",
        started_at=created_at, ended_at=created_at,
        method="prompt_template:fb_per_criterion_v4",
    )
    prov.used("feedback-generation", "submission_artifact")
    prov.used("feedback-generation", "rubric_artifact")
    prov.was_associated_with("feedback-generation", "feedback-agent")

    for fb in _feedback_sentences():
        ent = f"feedback_{fb['id']}"
        prov.add_entity(
            ent,
            f"Feedback sentence {fb['id']}",
            artifact_type="semantic_extraction",
        )
        prov.was_generated_by(ent, "feedback-generation")
        prov.was_derived_from(ent, "submission_artifact")
        prov.was_derived_from(ent, "rubric_artifact")

    digest = prov.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov.context_id}"
    builder._envelope.provenance_ref.prov_digest = digest

    envelope = builder.sign("did:university:feedback-agent").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t_start) * 1000
    metrics["statements"] = len(payload)
    metrics["feedback_sentences"] = len(_feedback_sentences())

    env_path = OUTPUT_DIR / "education_scenario_b_envelope.json"
    env_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    prov_path = OUTPUT_DIR / "education_scenario_b_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")

    metrics["envelope_size_bytes"] = env_path.stat().st_size

    print("=" * 60)
    print("EDUCATION SCENARIO B — Rubric-Grounded LLM Feedback")
    print("=" * 60)
    print(f"  Context ID:          {envelope.context_id}")
    print(f"  Statements:          {metrics['statements']} "
          f"(2 obs + {metrics['feedback_sentences']}x2 per-sentence + 1 situation)")
    print(f"  Envelope size:       {metrics['envelope_size_bytes']:,} bytes")
    print(f"  Envelope build time: {metrics['envelope_build_ms']:.1f} ms")
    print(f"  Output:              {env_path}")
    print("=" * 60)

    return metrics


if __name__ == "__main__":
    run()
