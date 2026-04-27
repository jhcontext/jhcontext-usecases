"""Unit tests for cohort-level helpers (feature-usage census + 4/5 rule)."""

from __future__ import annotations

import pytest

from jhcontext import (
    AbstractionLevel,
    EnvelopeBuilder,
    RiskLevel,
    TemporalScope,
)

from usecases.hiring.cohort import (
    feature_usage_census,
    four_fifths_ratio,
)


def _build_envelope(scope: str, categories: list[str], experience_band: str,
                    advanced: bool):
    """Lightweight envelope: scope, one DI block, semantic_payload group attribute."""
    return (
        EnvelopeBuilder()
        .set_producer("did:vendor:hiring-test")
        .set_scope(scope)
        .set_risk_level(RiskLevel.HIGH)
        .set_semantic_payload([{
            "experience_band": experience_band,
            "advanced_to_recruiter": advanced,
        }])
        .add_decision_influence(
            agent="screening-agent",
            categories=list(categories),
            influence_weights={c: 1.0 / len(categories) for c in categories},
            confidence=0.9,
            abstraction_level=AbstractionLevel.SITUATION,
            temporal_scope=TemporalScope.CURRENT,
        )
        .build()
    )


# ---------------------------------------------------------------------------
# feature_usage_census
# ---------------------------------------------------------------------------

def test_feature_usage_census_aggregates_categories():
    envs = [
        _build_envelope("screening->ranking", ["years_experience", "skills_overlap"],
                        "5-10y", True),
        _build_envelope("screening->ranking", ["years_experience", "tenure_pattern"],
                        ">15y", False),
        _build_envelope("screening->ranking", ["skills_overlap"], "5-10y", True),
    ]
    censuses = feature_usage_census(envs, handoff_filter="screening->ranking")
    assert len(censuses) == 1
    c = censuses[0]
    assert c.handoff == "screening->ranking"
    assert c.total_receipts == 3
    assert c.feature_counts["years_experience"] == 2
    assert c.feature_counts["skills_overlap"] == 2
    assert c.feature_counts["tenure_pattern"] == 1


def test_feature_usage_census_groups_by_scope_when_unfiltered():
    envs = [
        _build_envelope("screening->ranking", ["a"], "5-10y", True),
        _build_envelope("screening->ranking", ["b"], ">15y", False),
        _build_envelope("interview->evaluation", ["c"], "5-10y", True),
    ]
    censuses = feature_usage_census(envs)
    by_scope = {c.handoff: c for c in censuses}
    assert by_scope["screening->ranking"].total_receipts == 2
    assert by_scope["interview->evaluation"].total_receipts == 1


def test_feature_usage_census_filter_excludes_other_scopes():
    envs = [
        _build_envelope("screening->ranking", ["a"], "5-10y", True),
        _build_envelope("interview->evaluation", ["b"], "5-10y", True),
    ]
    censuses = feature_usage_census(envs, handoff_filter="screening->ranking")
    assert len(censuses) == 1
    assert censuses[0].total_receipts == 1


# ---------------------------------------------------------------------------
# four_fifths_ratio
# ---------------------------------------------------------------------------

def _advanced(env):
    for chunk in env.semantic_payload:
        if isinstance(chunk, dict) and "advanced_to_recruiter" in chunk:
            return bool(chunk["advanced_to_recruiter"])
    return False


def test_four_fifths_pass_balanced_rates():
    # protected (>15y): 8/10 advanced; reference (5-10y): 9/10 advanced
    envs = []
    for i in range(10):
        envs.append(_build_envelope("screening->ranking", ["x"], ">15y", i < 8))
    for i in range(10):
        envs.append(_build_envelope("screening->ranking", ["x"], "5-10y", i < 9))
    r = four_fifths_ratio(
        envs,
        group_attribute="experience_band",
        protected_value=">15y",
        reference_value="5-10y",
        advancement_predicate=_advanced,
    )
    assert r.protected_total == 10
    assert r.reference_total == 10
    assert r.protected_advanced == 8
    assert r.reference_advanced == 9
    assert r.ratio == pytest.approx(0.8 / 0.9, rel=1e-6)
    assert r.passed  # 0.888... >= 0.8


def test_four_fifths_fail_disparate_impact():
    """Seeded disparity matching paper's fixture: 18/100 vs 30/100 -> ratio 0.6."""
    envs = []
    for i in range(100):
        envs.append(_build_envelope("screening->ranking", ["x"], ">15y", i < 18))
    for i in range(100):
        envs.append(_build_envelope("screening->ranking", ["x"], "5-10y", i < 30))
    r = four_fifths_ratio(
        envs,
        group_attribute="experience_band",
        protected_value=">15y",
        reference_value="5-10y",
        advancement_predicate=_advanced,
    )
    assert r.selection_rate_protected == pytest.approx(0.18)
    assert r.selection_rate_reference == pytest.approx(0.30)
    assert r.ratio == pytest.approx(0.6, rel=1e-6)
    assert not r.passed


def test_four_fifths_excludes_unmatched_groups():
    envs = [
        _build_envelope("screening->ranking", ["x"], ">15y", True),
        _build_envelope("screening->ranking", ["x"], "5-10y", True),
        _build_envelope("screening->ranking", ["x"], "<2y", True),  # excluded
    ]
    r = four_fifths_ratio(
        envs,
        group_attribute="experience_band",
        protected_value=">15y",
        reference_value="5-10y",
        advancement_predicate=_advanced,
    )
    assert r.protected_total == 1
    assert r.reference_total == 1


def test_four_fifths_zero_reference_yields_zero_ratio():
    envs = [
        _build_envelope("screening->ranking", ["x"], ">15y", True),
    ]
    r = four_fifths_ratio(
        envs,
        group_attribute="experience_band",
        protected_value=">15y",
        reference_value="5-10y",
        advancement_predicate=_advanced,
    )
    assert r.reference_total == 0
    assert r.ratio == 0.0
    assert not r.passed
