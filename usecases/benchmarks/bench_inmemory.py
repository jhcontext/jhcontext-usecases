"""B1: In-memory baseline — envelope + PROV construction + audit (no persistence)."""

from __future__ import annotations

from jhcontext import (
    verify_integrity,
    verify_temporal_oversight,
    verify_negative_proof,
    verify_workflow_isolation,
)

from .config import ITERATIONS, WARMUP
from .helpers import timed, build_healthcare_envelope, build_education_envelope


def run() -> dict:
    print("  [B1] In-memory baseline...")

    # Healthcare
    def healthcare_build():
        return build_healthcare_envelope()

    build_health = timed(healthcare_build, ITERATIONS, WARMUP)

    env_h, prov_h = build_healthcare_envelope()

    def healthcare_audit():
        r1 = verify_temporal_oversight(
            prov_h, "ai-recommendation",
            ["review-ct-scan", "review-history", "review-pathology", "review-ai-recommendation"],
            min_review_seconds=300.0,
        )
        r2 = verify_integrity(env_h)
        return r1, r2

    audit_health = timed(healthcare_audit, ITERATIONS, WARMUP)

    # Education
    def education_build():
        return build_education_envelope()

    build_edu = timed(education_build, ITERATIONS, WARMUP)

    env_e, prov_g, prov_eq = build_education_envelope()

    def education_audit():
        r1 = verify_negative_proof(prov_g, "art-grade-result", ["biometric", "sensitive"])
        r2 = verify_workflow_isolation(prov_g, prov_eq)
        r3 = verify_integrity(env_e)
        return r1, r2, r3

    audit_edu = timed(education_audit, ITERATIONS, WARMUP)

    return {
        "healthcare_build": build_health,
        "healthcare_audit": audit_health,
        "education_build": build_edu,
        "education_audit": audit_edu,
    }
