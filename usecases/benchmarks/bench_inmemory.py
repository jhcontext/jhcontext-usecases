"""B1: In-memory baseline — envelope + PROV construction + audit (no persistence).

Measures both canonicalization modes:
  * URDNA2015         — W3C RDF Dataset Canonicalization (default, audit-grade).
  * deterministic-json — sorted-key compact JSON (~75x faster, syntax-only).
"""

from __future__ import annotations

import os

from jhcontext import (
    verify_integrity,
    verify_negative_proof,
    verify_temporal_oversight,
    verify_workflow_isolation,
)

from .config import ITERATIONS, WARMUP
from .helpers import build_education_envelope, build_healthcare_envelope, timed

CANONICALIZATION_MODES = ("URDNA2015", "deterministic-json")


def _measure_for_mode(mode: str) -> dict:
    """Run B1 fixtures for one canonicalization mode and return their stats."""
    prev = os.environ.get("JHCONTEXT_CANONICALIZATION")
    os.environ["JHCONTEXT_CANONICALIZATION"] = mode

    try:
        build_health = timed(build_healthcare_envelope, ITERATIONS, WARMUP)
        env_h, prov_h = build_healthcare_envelope()

        def healthcare_audit():
            r1 = verify_temporal_oversight(
                prov_h,
                "ai-recommendation",
                ["review-ct-scan", "review-history", "review-pathology",
                 "review-ai-recommendation"],
                min_review_seconds=300.0,
            )
            r2 = verify_integrity(env_h)
            return r1, r2

        audit_health = timed(healthcare_audit, ITERATIONS, WARMUP)

        build_edu = timed(build_education_envelope, ITERATIONS, WARMUP)
        env_e, prov_g, prov_eq = build_education_envelope()

        def education_audit():
            r1 = verify_negative_proof(prov_g, "art-grade-result",
                                       ["biometric", "sensitive"])
            r2 = verify_workflow_isolation(prov_g, prov_eq)
            r3 = verify_integrity(env_e)
            return r1, r2, r3

        audit_edu = timed(education_audit, ITERATIONS, WARMUP)
    finally:
        if prev is None:
            os.environ.pop("JHCONTEXT_CANONICALIZATION", None)
        else:
            os.environ["JHCONTEXT_CANONICALIZATION"] = prev

    return {
        "healthcare_build": build_health,
        "healthcare_audit": audit_health,
        "education_build": build_edu,
        "education_audit": audit_edu,
    }


def run() -> dict:
    print("  [B1] In-memory baseline...")
    from jhcontext.canonicalize import algorithm as _active_algorithm

    default_mode = _active_algorithm()
    results: dict = {}
    for mode in CANONICALIZATION_MODES:
        per_mode = _measure_for_mode(mode)
        # Flat keys reflect whatever the SDK's active default is.
        if mode == default_mode:
            results.update(per_mode)
        results[f"by_mode/{mode}"] = per_mode
    return results
