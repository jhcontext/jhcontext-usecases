"""HR-specific verifiers for the hiring pipeline.

Seven verifiers that complement the four domain-portable ones already shipped
in ``jhcontext.audit`` (``verify_negative_proof``, ``verify_temporal_oversight``,
``verify_workflow_isolation``, ``verify_pii_detachment``):

  1. verify_no_prohibited_practice    -- no model declares workplace-emotion
                                          inference or protected-attribute
                                          biometric categorisation
  2. verify_sourcing_neutrality       -- no prohibited ad-targeting parameter
                                          in the sourcing decision's chain
  3. verify_workforce_notice          -- collective-notice attestation present,
                                          signed, timestamped pre-deployment
  4. verify_candidate_notice          -- per-candidate notice attestation
                                          present and pre-decision
  5. verify_ai_literacy_attestation   -- the overseer's competence record is
                                          bound to the oversight activity
  6. verify_input_data_attestation    -- every model-bearing artifact carries
                                          a data-governance attestation
  7. verify_incident_attestation      -- every suspension activity has a
                                          downstream notification activity
                                          within 15 calendar days

Attestations are stored as ``Artifact`` entries in
``envelope.artifacts_registry`` with a ``metadata['kind']`` discriminator. This
keeps the SDK untouched (no ``ComplianceBlock.metadata`` extension required).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jhcontext import Envelope, PROVGraph
from jhcontext.audit import AuditResult


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_PROHIBITED_CAPABILITIES: tuple[str, ...] = (
    "workplace_emotion_inference",
    "protected_attribute_biometric_categorisation",
)

# Suspension -> notification window. Common regulatory pattern: 15 calendar
# days from suspension start to notification start.
NOTIFICATION_WINDOW_DAYS: int = 15

# JH-namespace activity attribute used to discriminate special activities
# (suspension, notification) inside the PROV graph.
ACTIVITY_KIND_ATTR: str = "kind"

# JH-namespace agent attributes used by verify_ai_literacy_attestation.
COMPETENCE_HASH_ATTR: str = "competenceRecordHash"
COMPETENCE_SIGNER_ATTR: str = "competenceRecordSigner"

# JH-namespace entity attribute used by verify_sourcing_neutrality.
AD_TARGETING_PARAM_ATTR: str = "adTargetingParam"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # rdflib preserves +00:00 form; datetime.fromisoformat handles it on 3.11+.
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _attestations(envelope: Envelope, kind: str) -> list[Any]:
    """Return artifacts in the envelope's registry whose metadata['kind'] matches."""
    return [
        a for a in envelope.artifacts_registry
        if a.metadata.get("kind") == kind
    ]


# ---------------------------------------------------------------------------
# 1. No prohibited practice (Art. 5(1)(f)/(g))
# ---------------------------------------------------------------------------

def verify_no_prohibited_practice(
    envelope: Envelope,
    prohibited_capabilities: list[str] | tuple[str, ...] | None = None,
) -> AuditResult:
    """Reject any model whose declared capabilities are banned at the workplace.

    Walks ``envelope.artifacts_registry`` and inspects each artifact's
    ``metadata['capabilities']`` list. Fails if any capability is in the
    ``prohibited_capabilities`` set. Defaults cover workplace-emotion
    inference and protected-attribute biometric categorisation -- the two
    capabilities banned in the workplace context by EU AI Act Art. 5(1)(f)
    and (g).
    """
    banned = set(prohibited_capabilities or DEFAULT_PROHIBITED_CAPABILITIES)
    violations: list[dict[str, Any]] = []
    artifacts_checked = 0

    for art in envelope.artifacts_registry:
        capabilities = art.metadata.get("capabilities") or []
        if not isinstance(capabilities, (list, tuple)):
            continue
        artifacts_checked += 1
        bad = sorted(set(capabilities) & banned)
        if bad:
            violations.append({
                "artifact_id": art.artifact_id,
                "model": art.model,
                "prohibited_capabilities": bad,
            })

    return AuditResult(
        check_name="no_prohibited_practice",
        passed=not violations,
        evidence={
            "artifacts_checked": artifacts_checked,
            "prohibited_capabilities": sorted(banned),
            "violations": violations,
        },
        message=(
            f"No prohibited practice: {artifacts_checked} artifacts free of "
            f"{len(banned)} banned capabilities"
            if not violations
            else f"PROHIBITED PRACTICE: {len(violations)} artifact(s) declare banned capabilities"
        ),
    )


# ---------------------------------------------------------------------------
# 2. Sourcing neutrality (Annex III §4(a))
# ---------------------------------------------------------------------------

def verify_sourcing_neutrality(
    prov: PROVGraph,
    sourcing_decision_entity_id: str,
    prohibited_targeting_attrs: list[str],
) -> AuditResult:
    """No prohibited ad-targeting parameter in the sourcing decision's chain.

    Walks ``prov.get_entities_in_chain(sourcing_decision_entity_id)`` and the
    decision entity itself. For every entity, reads all
    ``adTargetingParam`` JH attributes and fails if any value is in
    ``prohibited_targeting_attrs``.
    """
    chain = set(prov.get_entities_in_chain(sourcing_decision_entity_id))
    chain.add(sourcing_decision_entity_id)
    banned = set(prohibited_targeting_attrs)

    violations: list[dict[str, Any]] = []
    for entity_id in chain:
        uri = prov._uri(entity_id)
        params = [
            str(o) for o in prov._graph.objects(uri, prov._uri(AD_TARGETING_PARAM_ATTR))
        ]
        bad = sorted(set(params) & banned)
        if bad:
            violations.append({"entity": entity_id, "prohibited_params": bad})

    return AuditResult(
        check_name="sourcing_neutrality",
        passed=not violations,
        evidence={
            "decision_entity": sourcing_decision_entity_id,
            "chain_size": len(chain),
            "prohibited_targeting_attrs": sorted(banned),
            "violations": violations,
        },
        message=(
            f"Sourcing neutrality verified: {len(chain)} entities free of "
            f"{len(banned)} banned targeting params"
            if not violations
            else f"SOURCING-NEUTRALITY VIOLATION: {len(violations)} entity(ies) carry banned params"
        ),
    )


# ---------------------------------------------------------------------------
# 3. Workforce notice (Art. 26(7))
# ---------------------------------------------------------------------------

def verify_workforce_notice(envelope: Envelope) -> AuditResult:
    """Collective-notice attestation present, signed, and pre-deployment.

    Looks for an artifact whose ``metadata['kind'] ==
    'workforce_notice_attestation'``. Requires ``signer``,
    ``attestation_hash``, and ``attestation_timestamp`` -- and that the
    timestamp predates ``envelope.created_at`` (the notice must precede the
    receipt that claims to be governed by it).
    """
    matches = _attestations(envelope, "workforce_notice_attestation")
    if not matches:
        return AuditResult(
            check_name="workforce_notice",
            passed=False,
            evidence={"attestations_found": 0},
            message="WORKFORCE NOTICE: no attestation found in artifacts_registry",
        )

    issues: list[str] = []
    selected = matches[0]
    md = selected.metadata
    for required in ("signer", "attestation_hash", "attestation_timestamp"):
        if not md.get(required):
            issues.append(f"missing_{required}")

    notice_dt = _parse_iso(md.get("attestation_timestamp", ""))
    envelope_dt = _parse_iso(envelope.created_at)
    if notice_dt and envelope_dt and notice_dt >= envelope_dt:
        issues.append("notice_not_before_envelope_created_at")

    passed = not issues
    return AuditResult(
        check_name="workforce_notice",
        passed=passed,
        evidence={
            "attestation_artifact_id": selected.artifact_id,
            "signer": md.get("signer"),
            "attestation_timestamp": md.get("attestation_timestamp"),
            "envelope_created_at": envelope.created_at,
            "issues": issues,
        },
        message=(
            f"Workforce notice verified: signed by {md.get('signer')} "
            f"on {md.get('attestation_timestamp')}"
            if passed
            else f"WORKFORCE NOTICE FAILED: {', '.join(issues)}"
        ),
    )


# ---------------------------------------------------------------------------
# 4. Candidate notice (Art. 26(11) + Art. 50)
# ---------------------------------------------------------------------------

def verify_candidate_notice(
    envelope: Envelope,
    candidate_id: str,
) -> AuditResult:
    """Per-candidate notice present and timestamped before the decision.

    Looks for an artifact with ``metadata['kind'] ==
    'candidate_notice_attestation'`` and matching
    ``metadata['candidate_id']``. Also locates the decision artifact (either
    ``metadata['kind'] == 'decision'`` or, failing that, the artifact pointed
    at by ``envelope.passed_artifact_pointer``) and asserts that the notice
    timestamp predates the decision timestamp.
    """
    matches = [
        a for a in _attestations(envelope, "candidate_notice_attestation")
        if a.metadata.get("candidate_id") == candidate_id
    ]
    if not matches:
        return AuditResult(
            check_name="candidate_notice",
            passed=False,
            evidence={"candidate_id": candidate_id, "attestations_found": 0},
            message=f"CANDIDATE NOTICE: no attestation for candidate {candidate_id}",
        )

    selected = matches[0]
    md = selected.metadata
    issues: list[str] = []
    for required in ("signer", "attestation_timestamp"):
        if not md.get(required):
            issues.append(f"missing_{required}")

    # Locate decision artifact: prefer kind='decision', else passed_artifact_pointer
    decision_artifacts = _attestations(envelope, "decision")
    decision_art = decision_artifacts[0] if decision_artifacts else None
    if decision_art is None and envelope.passed_artifact_pointer:
        for a in envelope.artifacts_registry:
            if a.artifact_id == envelope.passed_artifact_pointer:
                decision_art = a
                break

    if decision_art is None:
        issues.append("no_decision_artifact")
    else:
        notice_dt = _parse_iso(md.get("attestation_timestamp", ""))
        decision_dt = _parse_iso(decision_art.timestamp)
        if notice_dt and decision_dt and notice_dt >= decision_dt:
            issues.append("notice_not_before_decision")

    passed = not issues
    return AuditResult(
        check_name="candidate_notice",
        passed=passed,
        evidence={
            "candidate_id": candidate_id,
            "attestation_artifact_id": selected.artifact_id,
            "signer": md.get("signer"),
            "attestation_timestamp": md.get("attestation_timestamp"),
            "decision_timestamp": decision_art.timestamp if decision_art else None,
            "issues": issues,
        },
        message=(
            f"Candidate notice verified: candidate {candidate_id} notified "
            f"on {md.get('attestation_timestamp')} (pre-decision)"
            if passed
            else f"CANDIDATE NOTICE FAILED ({candidate_id}): {', '.join(issues)}"
        ),
    )


# ---------------------------------------------------------------------------
# 5. AI literacy / overseer competence (Arts. 4 / 14(4))
# ---------------------------------------------------------------------------

def verify_ai_literacy_attestation(
    prov: PROVGraph,
    oversight_activity_id: str,
) -> AuditResult:
    """The agent overseeing the activity must carry a signed competence record.

    Locates every agent associated with the named activity (via PROV
    ``wasAssociatedWith``) and asserts that each carries both
    ``competenceRecordHash`` and ``competenceRecordSigner`` JH attributes.
    """
    from rdflib.namespace import PROV as PROV_NS

    activity_uri = prov._uri(oversight_activity_id)
    agent_uris = list(prov._graph.objects(activity_uri, PROV_NS.wasAssociatedWith))

    if not agent_uris:
        return AuditResult(
            check_name="ai_literacy_attestation",
            passed=False,
            evidence={"oversight_activity": oversight_activity_id, "agents": []},
            message=f"AI LITERACY: activity '{oversight_activity_id}' has no associated agent",
        )

    findings: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for agent_uri in agent_uris:
        agent_id = str(agent_uri).split("#")[-1] if "#" in str(agent_uri) else str(agent_uri)
        comp_hash = prov._graph.value(agent_uri, prov._uri(COMPETENCE_HASH_ATTR))
        comp_signer = prov._graph.value(agent_uri, prov._uri(COMPETENCE_SIGNER_ATTR))
        agent_issues: list[str] = []
        if not comp_hash:
            agent_issues.append("missing_competenceRecordHash")
        if not comp_signer:
            agent_issues.append("missing_competenceRecordSigner")
        record = {
            "agent": agent_id,
            "competenceRecordHash": str(comp_hash) if comp_hash else None,
            "competenceRecordSigner": str(comp_signer) if comp_signer else None,
        }
        findings.append(record)
        if agent_issues:
            issues.append({**record, "issues": agent_issues})

    passed = not issues
    return AuditResult(
        check_name="ai_literacy_attestation",
        passed=passed,
        evidence={
            "oversight_activity": oversight_activity_id,
            "agents": findings,
            "issues": issues,
        },
        message=(
            f"AI literacy verified: {len(findings)} overseer(s) competence-bound"
            if passed
            else f"AI LITERACY FAILED: {len(issues)} overseer(s) lack competence record"
        ),
    )


# ---------------------------------------------------------------------------
# 6. Input data governance (Art. 26(4))
# ---------------------------------------------------------------------------

def verify_input_data_attestation(envelope: Envelope) -> AuditResult:
    """Every model-bearing artifact must reference a data-governance attestation.

    For each artifact whose ``model`` field is set, requires
    ``metadata['data_governance_attestation_ref']`` and
    ``metadata['data_governance_attestation_signer']``. Both are deployer-
    side attestations of input-data representativeness for the role family.
    """
    model_artifacts = [a for a in envelope.artifacts_registry if a.model]
    if not model_artifacts:
        return AuditResult(
            check_name="input_data_attestation",
            passed=False,
            evidence={"model_artifacts": 0},
            message="INPUT DATA ATTESTATION: no model-bearing artifacts to attest",
        )

    missing: list[dict[str, Any]] = []
    for art in model_artifacts:
        md = art.metadata
        gaps: list[str] = []
        if not md.get("data_governance_attestation_ref"):
            gaps.append("missing_data_governance_attestation_ref")
        if not md.get("data_governance_attestation_signer"):
            gaps.append("missing_data_governance_attestation_signer")
        if gaps:
            missing.append({
                "artifact_id": art.artifact_id,
                "model": art.model,
                "issues": gaps,
            })

    passed = not missing
    return AuditResult(
        check_name="input_data_attestation",
        passed=passed,
        evidence={
            "model_artifacts": len(model_artifacts),
            "missing": missing,
        },
        message=(
            f"Input data attestation verified: {len(model_artifacts)} model artifact(s) bound"
            if passed
            else f"INPUT DATA ATTESTATION FAILED: {len(missing)} artifact(s) lack governance binding"
        ),
    )


# ---------------------------------------------------------------------------
# 7. Incident attestation (Art. 26(5) + Art. 73)
# ---------------------------------------------------------------------------

def verify_incident_attestation(
    prov: PROVGraph,
    notification_window_days: int = NOTIFICATION_WINDOW_DAYS,
) -> AuditResult:
    """Every suspension activity has a downstream notification within N days.

    Walks all activities; an activity tagged ``kind == 'suspension'`` must
    have at least one downstream activity (linked via PROV
    ``wasInformedBy``) with ``kind == 'art73_notification'`` whose
    ``startedAtTime`` is within ``notification_window_days`` calendar days
    of the suspension's ``startedAtTime``.
    """
    from rdflib.namespace import PROV as PROV_NS

    sequence = prov.get_temporal_sequence()
    activity_index = {a["activity_id"]: a for a in sequence}

    suspensions: list[dict[str, Any]] = []
    for act in sequence:
        uri = prov._uri(act["activity_id"])
        kind = prov._graph.value(uri, prov._uri(ACTIVITY_KIND_ATTR))
        if kind is not None and str(kind) == "suspension":
            suspensions.append(act)

    if not suspensions:
        return AuditResult(
            check_name="incident_attestation",
            passed=True,
            evidence={"suspensions": 0},
            message="No suspension events recorded; nothing to attest",
        )

    findings: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    window = timedelta(days=notification_window_days)

    for susp in suspensions:
        susp_id = susp["activity_id"]
        susp_uri = prov._uri(susp_id)
        susp_dt = _parse_iso(susp.get("started_at", ""))

        # Activities that wereInformedBy this suspension.
        informed_by_pairs = [
            str(s).split("#")[-1] if "#" in str(s) else str(s)
            for s in prov._graph.subjects(PROV_NS.wasInformedBy, susp_uri)
        ]

        notif_match: dict[str, Any] | None = None
        for candidate_id in informed_by_pairs:
            cand_uri = prov._uri(candidate_id)
            cand_kind = prov._graph.value(cand_uri, prov._uri(ACTIVITY_KIND_ATTR))
            if not cand_kind or str(cand_kind) != "art73_notification":
                continue
            cand_meta = activity_index.get(candidate_id, {})
            cand_dt = _parse_iso(cand_meta.get("started_at", ""))
            if susp_dt and cand_dt and cand_dt - susp_dt <= window and cand_dt >= susp_dt:
                notif_match = {
                    "activity_id": candidate_id,
                    "started_at": cand_meta.get("started_at"),
                    "delta_days": (cand_dt - susp_dt).days,
                }
                break

        record = {
            "suspension_activity": susp_id,
            "suspension_started_at": susp.get("started_at"),
            "notification": notif_match,
        }
        findings.append(record)
        if notif_match is None:
            failures.append(record)

    passed = not failures
    return AuditResult(
        check_name="incident_attestation",
        passed=passed,
        evidence={
            "suspensions": len(suspensions),
            "notification_window_days": notification_window_days,
            "findings": findings,
        },
        message=(
            f"Incident attestation verified: {len(suspensions)} suspension(s) all "
            f"notified within {notification_window_days} days"
            if passed
            else f"INCIDENT ATTESTATION FAILED: {len(failures)}/{len(suspensions)} "
                 f"suspension(s) lack timely notification"
        ),
    )


__all__ = [
    "DEFAULT_PROHIBITED_CAPABILITIES",
    "NOTIFICATION_WINDOW_DAYS",
    "verify_no_prohibited_practice",
    "verify_sourcing_neutrality",
    "verify_workforce_notice",
    "verify_candidate_notice",
    "verify_ai_literacy_attestation",
    "verify_input_data_attestation",
    "verify_incident_attestation",
]
