"""Deterministic synthetic data shared by all hiring scenarios.

No I/O, no randomness without a seed. Every fixture below returns plain
dataclasses or dicts so scenario scripts stay easy to read and the test
suite stays cheap.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Pipeline-wide constants
# ---------------------------------------------------------------------------

# Decision-influence weights at the screening->recruiter handoff (Scenario B).
# Identical numeric form across scenarios so the run_all.py cross-validation
# and the paper-figure parity test agree.
SCREENING_WEIGHTS: dict[str, float] = {
    "years_experience": 0.42,
    "skills_overlap": 0.31,
    "tenure_pattern": 0.18,
    "language_signal": 0.09,
}

# Identifiers suppressed at the screening->recruiter handoff. The recruiter
# never sees them; only their derived semantic statements cross the boundary.
SUPPRESSED_IDENTIFIERS: tuple[str, ...] = (
    "name",
    "photograph",
    "date_of_birth",
    "citizenship",
    "gender",
    "marital_status",
    "address",
)

# Targeting parameters that, if used to source candidates, would breach the
# Annex III §4(a) check enforced by verify_sourcing_neutrality.
PROHIBITED_TARGETING_ATTRS: tuple[str, ...] = (
    "inferred_age_bracket",
    "inferred_gender",
    "inferred_ethnicity",
    "inferred_disability",
    "inferred_pregnancy",
)


# ---------------------------------------------------------------------------
# Producers (DIDs of the six functional agents in the pipeline)
# ---------------------------------------------------------------------------

PRODUCERS: dict[str, str] = {
    "sourcing":         "did:vendor:sourcing-agent",
    "parsing":          "did:vendor:parsing-agent",
    "screening":        "did:vendor:screening-agent",
    "interview":        "did:vendor:interview-agent",
    "ranking":          "did:vendor:ranking-agent",
    "decision_support": "did:vendor:decision-support-agent",
}

DEPLOYER_SIGNER: str = "did:deployer:hr-director"
DPO_SIGNER:      str = "did:deployer:dpo"
COMPLIANCE_SIGNER: str = "did:deployer:compliance-officer"
RECRUITER_DID:   str = "did:deployer:recruiter-jane"
TRAINING_OFFICER_DID: str = "did:deployer:training-officer"


# ---------------------------------------------------------------------------
# Candidate fixture
# ---------------------------------------------------------------------------

EXPERIENCE_BANDS: tuple[str, ...] = ("<2y", "2-5y", "5-10y", "11-15y", ">15y")


@dataclass
class Candidate:
    candidate_id: str
    experience_band: str
    skills_overlap: float       # 0.0 - 1.0
    tenure_pattern_score: float
    language_signal_score: float
    advanced_to_recruiter: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "experience_band": self.experience_band,
            "skills_overlap": round(self.skills_overlap, 3),
            "tenure_pattern_score": round(self.tenure_pattern_score, 3),
            "language_signal_score": round(self.language_signal_score, 3),
            "advanced_to_recruiter": self.advanced_to_recruiter,
        }


def synthetic_candidates(n: int = 5, seed: int = 42) -> list[Candidate]:
    """Small deterministic candidate pool used by Scenario A."""
    rng = random.Random(seed)
    pool: list[Candidate] = []
    for i in range(n):
        band = rng.choice(EXPERIENCE_BANDS)
        pool.append(Candidate(
            candidate_id=f"cand-{i+1:04d}",
            experience_band=band,
            skills_overlap=rng.uniform(0.4, 0.95),
            tenure_pattern_score=rng.uniform(0.3, 0.9),
            language_signal_score=rng.uniform(0.2, 0.95),
            advanced_to_recruiter=rng.random() > 0.5,
        ))
    return pool


def shortlisted_candidates(n: int = 28, seed: int = 7) -> list[Candidate]:
    """Larger pool used by Scenario B (screening->recruiter handoff)."""
    rng = random.Random(seed)
    pool: list[Candidate] = []
    for i in range(n):
        band = rng.choice(EXPERIENCE_BANDS)
        pool.append(Candidate(
            candidate_id=f"cand-{i+1001:04d}",
            experience_band=band,
            skills_overlap=rng.uniform(0.55, 0.95),
            tenure_pattern_score=rng.uniform(0.5, 0.95),
            language_signal_score=rng.uniform(0.5, 0.95),
            advanced_to_recruiter=True,  # all 28 reach the recruiter stage
        ))
    return pool


def cohort_candidates(
    *,
    protected_count: int = 100,
    reference_count: int = 100,
    other_count: int = 112,
    protected_advance_rate: float = 0.18,
    reference_advance_rate: float = 0.30,
    seed: int = 17,
) -> list[Candidate]:
    """312-candidate corpus for Scenario C with seeded disparate impact.

    >15y stratum advances at 18 %; 5-10y stratum at 30 %; ratio 0.6 fails 4/5.
    """
    rng = random.Random(seed)
    pool: list[Candidate] = []
    counter = [2000]

    def _add(band: str, advance: bool) -> None:
        counter[0] += 1
        pool.append(Candidate(
            candidate_id=f"cand-{counter[0]:04d}",
            experience_band=band,
            skills_overlap=rng.uniform(0.4, 0.95),
            tenure_pattern_score=rng.uniform(0.3, 0.95),
            language_signal_score=rng.uniform(0.3, 0.95),
            advanced_to_recruiter=advance,
        ))

    p_advance = round(protected_count * protected_advance_rate)
    for i in range(protected_count):
        _add(">15y", i < p_advance)
    r_advance = round(reference_count * reference_advance_rate)
    for i in range(reference_count):
        _add("5-10y", i < r_advance)
    for i in range(other_count):
        band = rng.choice(["<2y", "2-5y", "11-15y"])
        _add(band, rng.random() < 0.25)

    return pool


# ---------------------------------------------------------------------------
# Vendor models / artifacts
# ---------------------------------------------------------------------------

@dataclass
class VendorModel:
    artifact_id: str
    model: str
    capabilities: list[str] = field(default_factory=list)
    data_governance_attestation_ref: str = "data-gov:role-fam-swe-2026Q1"
    data_governance_attestation_signer: str = DPO_SIGNER

    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.model.encode()).hexdigest()


def vendor_models(*, with_violation: bool = False) -> list[VendorModel]:
    """Three vendor models declared at procurement time (Scenario A).

    If *with_violation* is True, the interview model declares the
    (workplace-banned) workplace_emotion_inference capability so the
    no-prohibited-practice verifier fails.
    """
    interview_caps = ["transcript_extraction", "speech_to_text"]
    if with_violation:
        interview_caps.append("workplace_emotion_inference")
    return [
        VendorModel(
            artifact_id="art-model-screener",
            model="screener-v1.4",
            capabilities=["text_classification", "embedding", "ranking"],
        ),
        VendorModel(
            artifact_id="art-model-parser",
            model="cv-parser-v3.1",
            capabilities=["entity_extraction", "schema_mapping"],
        ),
        VendorModel(
            artifact_id="art-model-interview",
            model="interview-transcribe-v0.9",
            capabilities=interview_caps,
        ),
    ]


# ---------------------------------------------------------------------------
# Ad-targeting parameters used by Scenario A's sourcing handoff
# ---------------------------------------------------------------------------

def sourcing_targeting_params(*, with_violation: bool = False) -> list[str]:
    """Ad-targeting parameters set by the deployer at sourcing time."""
    base = ["geo:EU", "language:en", "industry:software", "seniority:mid-senior"]
    if with_violation:
        base.append("inferred_age_bracket")
    return base


# ---------------------------------------------------------------------------
# Attestation timestamps
# ---------------------------------------------------------------------------

@dataclass
class AttestationTimestamps:
    workforce_notice: datetime
    candidate_notice_offset: timedelta
    deployment_anchor: datetime


def default_attestation_timestamps(now: datetime | None = None) -> AttestationTimestamps:
    """Workforce notice 30 days before deployment; candidate notice 24 h pre-decision."""
    anchor = now or datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
    return AttestationTimestamps(
        workforce_notice=anchor - timedelta(days=30),
        candidate_notice_offset=timedelta(hours=24),
        deployment_anchor=anchor,
    )


# ---------------------------------------------------------------------------
# Suspension / Art. 73 notification fixtures (Scenario C)
# ---------------------------------------------------------------------------

@dataclass
class SuspensionEvent:
    suspension_id: str
    started_at: datetime
    notification_id: str | None
    notification_offset_days: int | None  # None if missing notification


def suspension_events(now: datetime | None = None) -> list[SuspensionEvent]:
    """Two suspensions: one with timely notification, one without."""
    anchor = now or datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
    return [
        SuspensionEvent(
            suspension_id="susp-2026-Q1-A",
            started_at=anchor,
            notification_id="notif-2026-Q1-A",
            notification_offset_days=10,
        ),
        SuspensionEvent(
            suspension_id="susp-2026-Q1-B",
            started_at=anchor + timedelta(days=45),
            notification_id=None,             # missing -> verifier should fail it
            notification_offset_days=None,
        ),
    ]


# ---------------------------------------------------------------------------
# Competence record (overseer's AI literacy attestation)
# ---------------------------------------------------------------------------

@dataclass
class CompetenceRecord:
    overseer_did: str
    competence_record_hash: str
    competence_record_signer: str


def recruiter_competence_record() -> CompetenceRecord:
    return CompetenceRecord(
        overseer_did=RECRUITER_DID,
        competence_record_hash="sha256:" + hashlib.sha256(
            b"competence-record-jane-2026Q1"
        ).hexdigest(),
        competence_record_signer=TRAINING_OFFICER_DID,
    )


__all__ = [
    "SCREENING_WEIGHTS",
    "SUPPRESSED_IDENTIFIERS",
    "PROHIBITED_TARGETING_ATTRS",
    "PRODUCERS",
    "DEPLOYER_SIGNER",
    "DPO_SIGNER",
    "COMPLIANCE_SIGNER",
    "RECRUITER_DID",
    "TRAINING_OFFICER_DID",
    "EXPERIENCE_BANDS",
    "Candidate",
    "synthetic_candidates",
    "shortlisted_candidates",
    "cohort_candidates",
    "VendorModel",
    "vendor_models",
    "sourcing_targeting_params",
    "AttestationTimestamps",
    "default_attestation_timestamps",
    "SuspensionEvent",
    "suspension_events",
    "CompetenceRecord",
    "recruiter_competence_record",
]
