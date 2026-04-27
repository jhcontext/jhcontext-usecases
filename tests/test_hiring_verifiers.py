"""Pass/fail unit tests for the seven HR-specific verifiers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jhcontext import (
    ArtifactType,
    EnvelopeBuilder,
    PROVGraph,
    RiskLevel,
)

from usecases.hiring.verifiers import (
    DEFAULT_PROHIBITED_CAPABILITIES,
    verify_ai_literacy_attestation,
    verify_candidate_notice,
    verify_incident_attestation,
    verify_input_data_attestation,
    verify_no_prohibited_practice,
    verify_sourcing_neutrality,
    verify_workforce_notice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_envelope(
    *,
    notice_ts: datetime | None = None,
    candidate_ts: datetime | None = None,
    candidate_id: str = "cand-001",
    decision_ts: datetime | None = None,
    capabilities: list[str] | None = None,
    model: str | None = "screener-v1",
    governance_ref: str | None = "data-gov:role-fam-swe",
    governance_signer: str | None = "did:vendor:dpo",
    workforce_signer: str | None = "did:hr:director",
):
    """Build a minimal envelope; per-test overrides toggle pass vs. fail paths."""
    builder = (
        EnvelopeBuilder()
        .set_producer("did:vendor:hiring-pipeline")
        .set_scope("hiring_test")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload([{"observations": [{"subject": candidate_id}]}])
    )

    # Workforce notice attestation
    if notice_ts is not None:
        builder.add_artifact(
            artifact_id="att-workforce",
            artifact_type=ArtifactType.TOOL_RESULT,
            content_hash="sha256:" + "0" * 64,
            kind="workforce_notice_attestation",
            signer=workforce_signer,
            attestation_hash="sha256:wf-notice-2026Q1",
            attestation_timestamp=_iso(notice_ts),
        )

    # Candidate notice attestation
    if candidate_ts is not None:
        builder.add_artifact(
            artifact_id="att-cand-001",
            artifact_type=ArtifactType.TOOL_RESULT,
            content_hash="sha256:" + "1" * 64,
            kind="candidate_notice_attestation",
            candidate_id=candidate_id,
            signer="did:vendor:notification-service",
            attestation_hash="sha256:cand-notice-001",
            attestation_timestamp=_iso(candidate_ts),
        )

    # Decision artifact (timestamped)
    if decision_ts is not None:
        builder.add_artifact(
            artifact_id="art-decision",
            artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
            content_hash="sha256:" + "2" * 64,
            kind="decision",
        )
        # Override the auto-set Artifact.timestamp to make it deterministic.
        builder._envelope.artifacts_registry[-1].timestamp = _iso(decision_ts)
        builder.set_passed_artifact("art-decision")

    # Model-bearing artifact
    if model is not None:
        kwargs = {
            "artifact_id": "art-model",
            "artifact_type": ArtifactType.SEMANTIC_EXTRACTION,
            "content_hash": "sha256:" + "3" * 64,
            "model": model,
            "capabilities": list(capabilities or []),
        }
        if governance_ref is not None:
            kwargs["data_governance_attestation_ref"] = governance_ref
        if governance_signer is not None:
            kwargs["data_governance_attestation_signer"] = governance_signer
        builder.add_artifact(**kwargs)

    return builder.sign("did:vendor:compliance-officer").build()


# ---------------------------------------------------------------------------
# 1. verify_no_prohibited_practice
# ---------------------------------------------------------------------------

def test_no_prohibited_practice_pass():
    env = _make_envelope(capabilities=["text_classification", "ranking"])
    r = verify_no_prohibited_practice(env)
    assert r.passed
    assert r.evidence["violations"] == []


def test_no_prohibited_practice_fail():
    env = _make_envelope(
        capabilities=["text_classification", "workplace_emotion_inference"],
    )
    r = verify_no_prohibited_practice(env)
    assert not r.passed
    assert len(r.evidence["violations"]) == 1
    assert "workplace_emotion_inference" in r.evidence["violations"][0]["prohibited_capabilities"]


def test_no_prohibited_practice_default_set_includes_biometric():
    env = _make_envelope(
        capabilities=["protected_attribute_biometric_categorisation"],
    )
    r = verify_no_prohibited_practice(env)
    assert not r.passed
    assert set(DEFAULT_PROHIBITED_CAPABILITIES).issubset(set(r.evidence["prohibited_capabilities"]))


# ---------------------------------------------------------------------------
# 2. verify_sourcing_neutrality
# ---------------------------------------------------------------------------

def _build_sourcing_graph(targeting_params: list[str]) -> tuple[PROVGraph, str]:
    prov = PROVGraph("ctx-sourcing-test")
    prov.add_entity("art-ad-config", "Sourcing ad config", artifact_type="config")
    prov.add_entity("art-sourcing-decision", "Sourcing decision",
                    artifact_type="semantic_extraction")
    prov.was_derived_from("art-sourcing-decision", "art-ad-config")
    for p in targeting_params:
        prov.set_entity_attribute("art-ad-config", "adTargetingParam", p)
    return prov, "art-sourcing-decision"


def test_sourcing_neutrality_pass():
    prov, decision = _build_sourcing_graph(targeting_params=["geo:EU", "language:en"])
    r = verify_sourcing_neutrality(
        prov, decision,
        prohibited_targeting_attrs=["inferred_age_bracket", "inferred_gender"],
    )
    assert r.passed
    assert r.evidence["violations"] == []


def test_sourcing_neutrality_fail():
    prov, decision = _build_sourcing_graph(
        targeting_params=["geo:EU", "inferred_age_bracket"],
    )
    r = verify_sourcing_neutrality(
        prov, decision,
        prohibited_targeting_attrs=["inferred_age_bracket", "inferred_gender"],
    )
    assert not r.passed
    assert len(r.evidence["violations"]) == 1
    assert "inferred_age_bracket" in r.evidence["violations"][0]["prohibited_params"]


# ---------------------------------------------------------------------------
# 3. verify_workforce_notice
# ---------------------------------------------------------------------------

def test_workforce_notice_pass():
    notice_ts = BASE - timedelta(days=30)  # 30 days before envelope
    env = _make_envelope(notice_ts=notice_ts)
    # envelope.created_at is set to "now" by EnvelopeBuilder; force it to BASE
    env.created_at = _iso(BASE)
    r = verify_workforce_notice(env)
    assert r.passed, r.message


def test_workforce_notice_fail_missing():
    env = _make_envelope()  # no notice_ts -> no attestation artifact
    r = verify_workforce_notice(env)
    assert not r.passed
    assert "no attestation" in r.message


def test_workforce_notice_fail_after_envelope():
    # Notice timestamped AFTER envelope creation -> fails timing check
    notice_ts = BASE + timedelta(days=1)
    env = _make_envelope(notice_ts=notice_ts)
    env.created_at = _iso(BASE)
    r = verify_workforce_notice(env)
    assert not r.passed
    assert "notice_not_before_envelope_created_at" in r.evidence["issues"]


# ---------------------------------------------------------------------------
# 4. verify_candidate_notice
# ---------------------------------------------------------------------------

def test_candidate_notice_pass():
    notice_ts = BASE - timedelta(hours=24)
    decision_ts = BASE
    env = _make_envelope(candidate_ts=notice_ts, decision_ts=decision_ts)
    r = verify_candidate_notice(env, candidate_id="cand-001")
    assert r.passed, r.message


def test_candidate_notice_fail_missing():
    env = _make_envelope(decision_ts=BASE)  # no candidate notice attestation
    r = verify_candidate_notice(env, candidate_id="cand-001")
    assert not r.passed


def test_candidate_notice_fail_after_decision():
    # Notice after decision -> fails
    decision_ts = BASE
    notice_ts = BASE + timedelta(hours=1)
    env = _make_envelope(candidate_ts=notice_ts, decision_ts=decision_ts)
    r = verify_candidate_notice(env, candidate_id="cand-001")
    assert not r.passed
    assert "notice_not_before_decision" in r.evidence["issues"]


# ---------------------------------------------------------------------------
# 5. verify_ai_literacy_attestation
# ---------------------------------------------------------------------------

def _build_oversight_graph(with_competence: bool) -> tuple[PROVGraph, str]:
    prov = PROVGraph("ctx-oversight-test")
    prov.add_agent("recruiter-jane", "Jane Doe", role="recruiter")
    prov.add_activity("recruiter-review", "Recruiter reviews shortlist",
                      started_at=_iso(BASE), ended_at=_iso(BASE + timedelta(minutes=10)))
    prov.was_associated_with("recruiter-review", "recruiter-jane")
    if with_competence:
        prov.set_entity_attribute(
            "recruiter-jane", "competenceRecordHash", "sha256:competence-jane",
        )
        prov.set_entity_attribute(
            "recruiter-jane", "competenceRecordSigner", "did:hr:training-officer",
        )
    return prov, "recruiter-review"


def test_ai_literacy_attestation_pass():
    prov, activity = _build_oversight_graph(with_competence=True)
    r = verify_ai_literacy_attestation(prov, activity)
    assert r.passed, r.message


def test_ai_literacy_attestation_fail_missing_record():
    prov, activity = _build_oversight_graph(with_competence=False)
    r = verify_ai_literacy_attestation(prov, activity)
    assert not r.passed
    issues = r.evidence["issues"]
    assert issues and "missing_competenceRecordHash" in issues[0]["issues"]


def test_ai_literacy_attestation_fail_no_agent():
    prov = PROVGraph("ctx-oversight-orphan")
    prov.add_activity("orphan-activity", "No-agent activity",
                      started_at=_iso(BASE))
    r = verify_ai_literacy_attestation(prov, "orphan-activity")
    assert not r.passed
    assert "no associated agent" in r.message


# ---------------------------------------------------------------------------
# 6. verify_input_data_attestation
# ---------------------------------------------------------------------------

def test_input_data_attestation_pass():
    env = _make_envelope(model="screener-v1")
    r = verify_input_data_attestation(env)
    assert r.passed, r.message


def test_input_data_attestation_fail_missing_ref():
    env = _make_envelope(model="screener-v1", governance_ref=None)
    r = verify_input_data_attestation(env)
    assert not r.passed
    assert any("missing_data_governance_attestation_ref" in m["issues"]
               for m in r.evidence["missing"])


def test_input_data_attestation_fail_no_model_artifacts():
    env = _make_envelope(model=None)
    r = verify_input_data_attestation(env)
    assert not r.passed
    assert r.evidence["model_artifacts"] == 0


# ---------------------------------------------------------------------------
# 7. verify_incident_attestation
# ---------------------------------------------------------------------------

def _build_incident_graph(
    *,
    notify_within_days: int | None,
) -> PROVGraph:
    prov = PROVGraph("ctx-incident-test")
    susp_start = BASE
    prov.add_activity("susp-2026-Q2", "Model suspension",
                      started_at=_iso(susp_start),
                      ended_at=_iso(susp_start + timedelta(hours=1)))
    prov.set_entity_attribute("susp-2026-Q2", "kind", "suspension")

    if notify_within_days is not None:
        notif_start = susp_start + timedelta(days=notify_within_days)
        prov.add_activity("notif-2026-Q2", "Art. 73 notification to authority",
                          started_at=_iso(notif_start),
                          ended_at=_iso(notif_start + timedelta(hours=1)))
        prov.set_entity_attribute("notif-2026-Q2", "kind", "art73_notification")
        prov.was_informed_by("notif-2026-Q2", "susp-2026-Q2")
    return prov


def test_incident_attestation_pass_within_window():
    prov = _build_incident_graph(notify_within_days=10)
    r = verify_incident_attestation(prov)
    assert r.passed, r.message
    assert r.evidence["suspensions"] == 1


def test_incident_attestation_fail_outside_window():
    prov = _build_incident_graph(notify_within_days=20)
    r = verify_incident_attestation(prov)
    assert not r.passed


def test_incident_attestation_fail_missing_notification():
    prov = _build_incident_graph(notify_within_days=None)
    r = verify_incident_attestation(prov)
    assert not r.passed


def test_incident_attestation_pass_no_suspensions():
    """Empty corpus: nothing to attest, vacuously passes."""
    prov = PROVGraph("ctx-no-incidents")
    r = verify_incident_attestation(prov)
    assert r.passed
    assert r.evidence["suspensions"] == 0
