"""Rubric-Grounded Oral Feedback (multimodal extension of Scenario B).

Offline simulation of an oral-English assessment pipeline. The student
submits an audio recording; a transcription + forced-alignment stage
produces a word-level timing map; an LLM feedback agent emits per-
criterion feedback where every sentence is bound to a millisecond window
on the original audio artifact (not a character offset on text).

No STT model is invoked — the transcript and word-timings are a fixed
fixture so the scenario is reproducible offline.

Envelope model: one handoff → one envelope. The feedback-generation
handoff emits ONE envelope per submission whose UserML
``semantic_payload`` carries a list of N interpretation-layer entries
(one per feedback sentence). Per-sentence PROV entities are still
recorded so ``verify_multimodal_binding`` can audit each sentence
case-by-case.

Pipeline: audio submission -> transcription+alignment -> LLM feedback ->
one envelope per submission (with N interpretation entries) ->
multimodal-binding audit over PROV entities.

Verifiers exercised:
  - verify_multimodal_binding   (modality-aware rubric+evidence binding)
  - verify_integrity            (envelope signatures + content hashes)

Outputs (usecases/output/):
  oral_feedback_envelopes.jsonl   One envelope per submission
  oral_feedback_prov.ttl          W3C PROV graph (oral grading chain)
  oral_feedback_audit.json        Audit report
  oral_feedback_metrics.json      Timing + benchmark metrics
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
    verify_multimodal_binding,
    generate_audit_report,
)
from jhcontext.pii import InMemoryPIIVault
from jhcontext.crypto import compute_sha256

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# -- Rubric + submission fixtures ---------------------------------------------

RUBRIC_ID = "oral_rubric_v1.0"
RUBRIC_CRITERIA = [
    "C1-pronunciation",
    "C2-fluency",
    "C3-content_coherence",
    "C4-grammar_range",
]
STT_MODEL_ID = "whisper-large-v3"
FEEDBACK_MODEL_ID = "gpt-4o-2024-08-06"
PROMPT_TEMPLATE_ID = "oral_fb_per_criterion_v1"

# Fixed-fixture 28-second oral presentation: (word, start_ms, end_ms).
# The transcript is the concatenation of these words; the audio artifact
# is content-addressed by a synthetic SHA-256 of the waveform.
WORD_TIMINGS: list[tuple[str, int, int]] = [
    ("Today",      450,   900),
    ("I",          950,  1050),
    ("would",     1100,  1350),
    ("like",      1400,  1600),
    ("to",        1650,  1750),
    ("argue",     1800,  2200),
    ("that",      2250,  2400),
    ("carbon",    2450,  2850),
    ("pricing",   2900,  3400),
    ("is",        3450,  3600),
    ("politically", 3650, 4250),
    ("difficult", 4300,  4800),
    ("but",       4850,  5000),
    ("effective", 5050,  5600),
    ("in",        5650,  5750),
    ("jurisdictions", 5800, 6500),
    ("that",      6550,  6700),
    ("adopt",     6750,  7100),
    ("it",        7150,  7280),
    # segment boundary (pause)
    ("The",       8000,  8200),
    ("British",   8250,  8650),
    ("Columbia",  8700,  9300),
    ("example",   9350,  9900),
    ("shows",     9950, 10300),
    ("a",        10350, 10420),
    ("nine",     10470, 10750),
    ("percent",  10800, 11250),
    ("reduction",11300, 11950),
    ("without",  12000, 12450),
    ("harming",  12500, 12950),
    ("GDP",      13000, 13400),
    # segment boundary
    ("However",  14500, 15100),
    ("equity",   15150, 15650),
    ("effects",  15700, 16200),
    ("have",     16250, 16450),
    ("been",     16500, 16700),
    ("mixed",    16750, 17200),
    ("and",      17250, 17400),
    ("rebates",  17450, 17900),
    ("can",      17950, 18150),
    ("help",     18200, 18550),
]
AUDIO_DURATION_MS = 19_000

# Build the transcript artifact from WORD_TIMINGS so verifier-reading and
# hashing use the same source of truth.
TRANSCRIPT_TEXT = " ".join(w for (w, _s, _e) in WORD_TIMINGS)

# Synthetic audio "bytes" — a deterministic stand-in for the waveform.
# In production this would be the real PCM stream; the only thing the
# protocol needs is a stable content hash.
AUDIO_BYTES = f"pcm-fixture:{AUDIO_DURATION_MS}ms:{len(WORD_TIMINGS)}words".encode("utf-8")


def _audio_content_hash() -> str:
    return "sha256:" + compute_sha256(AUDIO_BYTES)[:16] + "..."


def _span_for_word_range(start_idx: int, end_idx: int) -> tuple[int, int, str]:
    """Return (start_ms, end_ms, phrase) for a word-index range (inclusive)."""
    start_ms = WORD_TIMINGS[start_idx][1]
    end_ms = WORD_TIMINGS[end_idx][2]
    phrase = " ".join(w for (w, _s, _e) in WORD_TIMINGS[start_idx:end_idx + 1])
    return start_ms, end_ms, phrase


def _evidence_span_hash(audio_bytes: bytes, start_ms: int, end_ms: int) -> str:
    """Hash of the (conceptual) audio slice [start_ms, end_ms).

    For this offline fixture we hash the tuple (content_hash, start_ms,
    end_ms) — which is cryptographically equivalent to hashing a byte
    slice at a fixed sample rate (an auditor can recompute).
    """
    payload = f"{compute_sha256(audio_bytes)}:{start_ms}:{end_ms}".encode("utf-8")
    return "sha256:" + compute_sha256(payload)[:16] + "..."


def _feedback_sentences() -> list[dict]:
    """Return 6 LLM-generated feedback sentences for the oral presentation.

    Five are rubric+audio-grounded (each cites a real word-range in
    WORD_TIMINGS); one is an orphan that claims to assess a criterion
    without a valid audio span. The verifier flags the orphan.
    """
    # (criterion, start_word_idx, end_word_idx, prose_preview, grounded)
    spans = [
        ("C2-fluency",        0,  6,  "Opening phrase is clear and well-paced.", True),
        ("C1-pronunciation",  7,  8,  "'Carbon pricing' pronounced clearly.",    True),
        ("C3-content_coherence", 19, 30, "British Columbia example supports the thesis.", True),
        ("C4-grammar_range",  31, 37, "Contrast marker 'However' introduces counterpoint.", True),
        ("C2-fluency",        32, 40, "Final segment maintains pacing through the pivot.", True),
        # orphan
        ("C1-pronunciation", None, None, None, False),
    ]
    audio_hash = _audio_content_hash()
    sentences: list[dict] = []
    for i, (crit, s_idx, e_idx, preview, grounded) in enumerate(spans, start=1):
        fs: dict = {
            "id": f"art-oral-fb-sentence-{i:02d}",
            "criterion_id": f"{RUBRIC_ID}#{crit}",
            "text_hash": "sha256:" + compute_sha256(f"oral-fb-{i}".encode("utf-8"))[:16] + "...",
            "model_id": FEEDBACK_MODEL_ID,
            "prompt_template_id": PROMPT_TEMPLATE_ID,
            "prompt_template_hash": "sha256:" + compute_sha256(
                PROMPT_TEMPLATE_ID.encode("utf-8")
            )[:16] + "...",
            "modality": "audio",
            "grounded": grounded,
        }
        if grounded:
            start_ms, end_ms, phrase = _span_for_word_range(s_idx, e_idx)
            fs["evidence_start_ms"] = start_ms
            fs["evidence_end_ms"] = end_ms
            fs["evidence_span_hash"] = _evidence_span_hash(AUDIO_BYTES, start_ms, end_ms)
            fs["cited_phrase"] = phrase
            fs["cited_preview"] = preview
        sentences.append(fs)
    return sentences


# -- Scenario: one oral submission with full-detail envelopes ----------------

def _build_scenario_envelopes(
    submission_id: str,
    sentences: list[dict],
    ts_base: datetime,
    prov: PROVGraph | None = None,
    emit_envelopes: bool = True,
):
    """Build ONE envelope per submission (all feedback sentences bundled).

    The feedback-generation handoff produces one signed PAC-AI envelope
    whose UserML ``semantic_payload`` carries a list of interpretation
    entries — one per feedback sentence — together with a shared
    situation entry and application-layer feedback texts.
    Per-sentence PROV entities are still emitted so verifiers audit
    each sentence individually.

    Returns ``(envelopes, prov)`` where ``envelopes`` is a list of length
    0 or 1 (0 when ``emit_envelopes=False``).
    """
    audio_hash = _audio_content_hash()
    submission_entity = f"art-oral-submission-{submission_id}"
    transcript_entity = f"art-transcript-{submission_id}"
    rubric_entity = f"art-oral-rubric-{RUBRIC_ID}"

    if prov is None:
        prov = PROVGraph(f"ctx-edu-oral-feedback-{submission_id}")

    # The audio artifact — native AUDIO type
    prov.add_entity(
        submission_entity,
        f"Oral submission {submission_id} ({AUDIO_DURATION_MS}ms)",
        artifact_type="audio",
        content_hash=audio_hash,
    )
    # Transcript + word-timings — derived from the audio
    prov.add_entity(
        transcript_entity,
        f"Whisper transcript + alignment for {submission_id}",
        artifact_type="semantic_extraction",
        content_hash="sha256:" + compute_sha256(TRANSCRIPT_TEXT.encode("utf-8"))[:16] + "...",
    )
    prov.add_agent("stt-agent", "Speech-to-Text + Alignment Agent", role="transcriber")
    prov.add_activity(
        f"transcription-{submission_id}",
        "Audio transcription and forced alignment",
        started_at=_iso(ts_base),
        ended_at=_iso(ts_base + timedelta(seconds=3)),
        method=f"STT ({STT_MODEL_ID}) + forced alignment",
    )
    prov.used(f"transcription-{submission_id}", submission_entity)
    prov.was_generated_by(transcript_entity, f"transcription-{submission_id}")
    prov.was_derived_from(transcript_entity, submission_entity)
    prov.was_associated_with(f"transcription-{submission_id}", "stt-agent")

    # Rubric artifact
    prov.add_entity(
        rubric_entity,
        f"Oral-assessment rubric {RUBRIC_ID}",
        artifact_type="token_sequence",
        content_hash="sha256:" + compute_sha256(RUBRIC_ID.encode("utf-8"))[:16] + "...",
    )

    # Feedback generation activity (single handoff, regardless of how
    # many sentences it produces).
    prov.add_agent("oral-feedback-agent", "LLM Oral Feedback Agent", role="feedback_generator")
    feedback_activity = f"oral-feedback-generation-{submission_id}"
    prov.add_activity(
        feedback_activity,
        "Per-criterion oral feedback generation",
        started_at=_iso(ts_base + timedelta(seconds=3)),
        ended_at=_iso(ts_base + timedelta(seconds=8)),
        method=f"LLM inference ({FEEDBACK_MODEL_ID})",
    )
    prov.used(feedback_activity, submission_entity)
    prov.used(feedback_activity, transcript_entity)
    prov.used(feedback_activity, rubric_entity)
    prov.was_associated_with(feedback_activity, "oral-feedback-agent")

    # Per-sentence PROV entities (audit granularity preserved).
    for fs in sentences:
        prov.add_entity(
            fs["id"],
            f"Oral feedback sentence assessing {fs['criterion_id']}",
            artifact_type="semantic_extraction",
            content_hash=fs["text_hash"],
        )
        prov.was_generated_by(fs["id"], feedback_activity)
        prov.was_derived_from(fs["id"], submission_entity)
        prov.was_derived_from(fs["id"], rubric_entity)

        prov.set_entity_attribute(fs["id"], "rubricCriterionId", fs["criterion_id"])
        prov.set_entity_attribute(fs["id"], "modelVersion", fs["model_id"])
        prov.set_entity_attribute(fs["id"], "promptTemplateHash", fs["prompt_template_hash"])
        prov.set_entity_attribute(fs["id"], "artifactModality", fs["modality"])
        if fs["grounded"]:
            prov.set_entity_attribute(fs["id"], "evidenceStartMs", fs["evidence_start_ms"])
            prov.set_entity_attribute(fs["id"], "evidenceEndMs", fs["evidence_end_ms"])
            prov.set_entity_attribute(fs["id"], "evidenceSpanHash", fs["evidence_span_hash"])

    if not emit_envelopes:
        return [], prov

    # Build the SINGLE envelope for the feedback-generation handoff.
    # UserML layered payload: interpretation carries per-sentence
    # rubric-binding; application carries the sentence texts; situation
    # records the assessment type.
    interpretations = []
    application_entries = []
    for fs in sentences:
        interpretations.append(interpretation(
            fs["id"], "addresses_criterion", fs["criterion_id"],
            confidence=0.85,
        ))
        if fs["grounded"]:
            interpretations.append(interpretation(
                fs["id"], "evidence_window",
                {
                    "start_ms": fs["evidence_start_ms"],
                    "end_ms": fs["evidence_end_ms"],
                    "hash": fs["evidence_span_hash"],
                    "modality": fs["modality"],
                },
                confidence=1.0,
            ))
        application_entries.append({
            "predicate": "feedback_sentence",
            "subject": fs["id"],
            "object": fs.get("cited_preview") or "<feedback text elided>",
        })

    builder = (
        EnvelopeBuilder()
        .set_producer("did:university:oral-feedback-agent-v1.0")
        .set_scope("education_oral_assessment")
        .set_ttl("P5Y")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([
            userml_payload(
                observations=[
                    observation(submission_entity, "duration_ms", AUDIO_DURATION_MS),
                    observation(submission_entity, "word_count", len(WORD_TIMINGS)),
                ],
                interpretations=interpretations,
                situations=[
                    {"subject": submission_entity,
                     "predicate": "isInSituation",
                     "object": "summative_oral_assessment",
                     "confidence": 0.95},
                ],
                application=application_entries,
            ),
        ])
        .add_artifact(
            artifact_id=submission_entity,
            artifact_type=ArtifactType.AUDIO,
            content_hash=audio_hash,
        )
        .add_artifact(
            artifact_id=transcript_entity,
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:" + compute_sha256(TRANSCRIPT_TEXT.encode("utf-8"))[:16] + "...",
            model=STT_MODEL_ID,
        )
        .add_artifact(
            artifact_id=rubric_entity,
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:" + compute_sha256(RUBRIC_ID.encode("utf-8"))[:16] + "...",
        )
        .add_artifact(
            artifact_id=f"art-prompt-{PROMPT_TEMPLATE_ID}",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:" + compute_sha256(PROMPT_TEMPLATE_ID.encode("utf-8"))[:16] + "...",
            model=FEEDBACK_MODEL_ID,
        )
        .set_passed_artifact(transcript_entity)
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
            model_card_ref="https://university.example/models/oral-feedback-v1.0",
            escalation_path="academic-affairs@university.example",
        )
    )
    envelope = builder.sign("did:university:compliance-officer").build()
    return [envelope], prov


# -- Classroom-scale benchmark ------------------------------------------------

def _benchmark_envelope_construction(
    n_submissions: int = 500,
    sentences_per_submission: int = 6,
) -> dict:
    """Construct one envelope per submission (block-level). Each envelope
    bundles ``sentences_per_submission`` interpretation entries.

    Classroom-scale sizing: for a 500-submission assignment the protocol
    emits 500 envelopes total (not 500 × N), each carrying the
    per-sentence bindings as UserML interpretation-layer entries.
    """
    ts_base = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    total_envelopes = 0
    total_interpretation_entries = 0
    t0 = time.perf_counter()
    for s_idx in range(n_submissions):
        sub_id = f"S-{s_idx:05d}"
        sentences = _feedback_sentences()[:sentences_per_submission]
        envelopes, _ = _build_scenario_envelopes(
            submission_id=sub_id,
            sentences=sentences,
            ts_base=ts_base + timedelta(seconds=s_idx),
        )
        total_envelopes += len(envelopes)
        total_interpretation_entries += len(sentences) * 2  # addresses_criterion + evidence_window
    elapsed_s = time.perf_counter() - t0
    return {
        "n_submissions": n_submissions,
        "sentences_per_submission": sentences_per_submission,
        "envelopes_per_submission": 1,
        "total_envelopes": total_envelopes,
        "total_interpretation_entries": total_interpretation_entries,
        "elapsed_seconds": elapsed_s,
        "per_envelope_ms": (elapsed_s * 1000) / max(1, total_envelopes),
        "per_submission_ms": (elapsed_s * 1000) / max(1, n_submissions),
    }


# -- Main ---------------------------------------------------------------------

def run() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    # 1. Full-detail run for ONE oral submission
    ts_base = datetime(2026, 5, 14, 10, 32, 17, tzinfo=timezone.utc)
    sentences = _feedback_sentences()
    envelopes, prov = _build_scenario_envelopes(
        submission_id="ORAL-98765",
        sentences=sentences,
        ts_base=ts_base,
    )

    # 2. Audit
    t0 = time.perf_counter()
    multimodal_check = verify_multimodal_binding(
        prov,
        feedback_sentence_ids=[s["id"] for s in sentences],
        submission_entity_id="art-oral-submission-ORAL-98765",
        modality="audio",
    )
    integrity_checks = [verify_integrity(e) for e in envelopes]
    integrity_passed = all(c.passed for c in integrity_checks)
    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000

    # 3. Classroom-scale benchmark
    bench = _benchmark_envelope_construction(n_submissions=500, sentences_per_submission=6)
    metrics["benchmark"] = bench

    # 4. Persist outputs
    env_path = OUTPUT_DIR / "oral_feedback_envelopes.jsonl"
    with env_path.open("w", encoding="utf-8") as f:
        for e in envelopes:
            f.write(json.dumps(e.to_jsonld(), ensure_ascii=False) + "\n")
    metrics["envelopes_emitted"] = len(envelopes)

    prov_path = OUTPUT_DIR / "oral_feedback_prov.ttl"
    prov_path.write_text(prov.serialize("turtle"), encoding="utf-8")

    audit_report = {
        "context_id": f"ctx-edu-oral-feedback-ORAL-98765",
        "multimodal_binding": {
            "passed": multimodal_check.passed,
            "message": multimodal_check.message,
            "evidence": multimodal_check.evidence,
        },
        "integrity": {
            "passed": integrity_passed,
            "envelopes_checked": len(integrity_checks),
        },
    }
    audit_path = OUTPUT_DIR / "oral_feedback_audit.json"
    audit_path.write_text(
        json.dumps(audit_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000
    metrics_path = OUTPUT_DIR / "oral_feedback_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # 5. Print summary
    print("=" * 68)
    print("SCENARIO B (multimodal) — Rubric-Grounded Oral Feedback")
    print("=" * 68)
    print(f"  Submission:          ORAL-98765 ({AUDIO_DURATION_MS}ms audio)")
    print(f"  Rubric:              {RUBRIC_ID} ({len(RUBRIC_CRITERIA)} criteria)")
    print(f"  STT model:           {STT_MODEL_ID}")
    print(f"  Feedback model:      {FEEDBACK_MODEL_ID}")
    print(f"  Feedback sentences:  {len(sentences)}")
    print(f"  Envelopes emitted:   {len(envelopes)} (one per submission; "
          f"sentences bundled as UserML interpretation entries)")
    print()
    print("  AUDIT RESULTS:")
    status = "PASS" if multimodal_check.passed else "FAIL"
    print(f"    [{status}] multimodal_binding: {multimodal_check.message}")
    print(f"    [{'PASS' if integrity_passed else 'FAIL'}] integrity: "
          f"{len(integrity_checks)} envelopes verified")
    if multimodal_check.evidence.get("orphans"):
        print("    Orphan sentences flagged (expected: 1 intentional orphan):")
        for o in multimodal_check.evidence["orphans"]:
            print(f"      - {o['feedback_sentence']}: {', '.join(o['issues'])}")
    print()
    print(f"  CLASSROOM-SCALE BENCHMARK ({bench['n_submissions']} submissions × "
          f"{bench['sentences_per_submission']} sentences/sub):")
    print(f"    Envelopes emitted:   {bench['total_envelopes']:,} "
          f"(1 per submission, bundled)")
    print(f"    Interpretation entries across envelopes: "
          f"{bench['total_interpretation_entries']:,}")
    print(f"    Aggregate time:      {bench['elapsed_seconds']:.2f} s")
    print(f"    Per-submission cost: {bench['per_submission_ms']:.2f} ms")
    print(f"    Per-envelope cost:   {bench['per_envelope_ms']:.2f} ms")
    print()
    print(f"  Outputs saved to: {OUTPUT_DIR}/")
    print("=" * 68)
    return metrics


if __name__ == "__main__":
    run()
