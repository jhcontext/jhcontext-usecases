"""Cohort analytics over a corpus of receipts.

Two helpers consume a list of envelopes (the receipt corpus) and return
corpus-level findings -- distinct from single-receipt verifiers because the
unit of analysis is the population, not the individual decision:

  * feature_usage_census  -- count how often each feature category appears
                              across the decision_influence blocks of all
                              receipts at a given handoff
  * four_fifths_ratio     -- compare advancement rates between two strata of
                              the population (US EEOC four-fifths guideline)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from jhcontext import Envelope


# ---------------------------------------------------------------------------
# Feature-usage census
# ---------------------------------------------------------------------------

@dataclass
class FeatureUsageCensus:
    """Per-handoff aggregate of decision-influence feature categories."""
    handoff: str
    total_receipts: int
    feature_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff": self.handoff,
            "total_receipts": self.total_receipts,
            "feature_counts": dict(self.feature_counts),
        }


def feature_usage_census(
    envelopes: list[Envelope],
    handoff_filter: str | None = None,
) -> list[FeatureUsageCensus]:
    """Aggregate feature-category usage across a corpus.

    Groups envelopes by ``envelope.scope`` (treated as the handoff label).
    Within each group, sums the ``categories`` lists across every
    ``DecisionInfluence`` block. Returns one :class:`FeatureUsageCensus` per
    distinct scope; if ``handoff_filter`` is given, only envelopes whose
    scope equals it are kept.
    """
    if handoff_filter is not None:
        envelopes = [e for e in envelopes if e.scope == handoff_filter]

    grouped: dict[str, list[Envelope]] = {}
    for env in envelopes:
        grouped.setdefault(env.scope, []).append(env)

    results: list[FeatureUsageCensus] = []
    for scope, group in sorted(grouped.items()):
        counts: Counter[str] = Counter()
        for env in group:
            for di in env.decision_influence:
                for cat in di.categories:
                    counts[cat] += 1
        results.append(FeatureUsageCensus(
            handoff=scope,
            total_receipts=len(group),
            feature_counts=dict(counts.most_common()),
        ))
    return results


# ---------------------------------------------------------------------------
# Four-fifths disparate-impact test (US EEOC guideline)
# ---------------------------------------------------------------------------

@dataclass
class FourFifthsResult:
    """Result of a four-fifths-rule comparison between two strata."""
    group_attribute: str
    protected_value: str
    reference_value: str
    protected_total: int
    protected_advanced: int
    reference_total: int
    reference_advanced: int
    selection_rate_protected: float
    selection_rate_reference: float
    ratio: float
    passed: bool  # ratio >= 0.8

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_attribute": self.group_attribute,
            "protected_value": self.protected_value,
            "reference_value": self.reference_value,
            "protected_total": self.protected_total,
            "protected_advanced": self.protected_advanced,
            "reference_total": self.reference_total,
            "reference_advanced": self.reference_advanced,
            "selection_rate_protected": self.selection_rate_protected,
            "selection_rate_reference": self.selection_rate_reference,
            "ratio": self.ratio,
            "passed": self.passed,
        }


def _read_group_attribute(env: Envelope, attribute: str) -> str | None:
    """Find the first dict in semantic_payload that carries ``attribute``."""
    for chunk in env.semantic_payload:
        if not isinstance(chunk, dict):
            continue
        if attribute in chunk:
            return str(chunk[attribute])
        # UserML payloads nest under "observations" / "interpretations" /
        # "situations" / "applications" -- walk one level.
        for inner in chunk.values():
            if isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict) and attribute in item:
                        return str(item[attribute])
    return None


def four_fifths_ratio(
    envelopes: list[Envelope],
    group_attribute: str,
    protected_value: str,
    reference_value: str,
    advancement_predicate: Callable[[Envelope], bool],
) -> FourFifthsResult:
    """Compute the four-fifths advancement-rate ratio between two strata.

    Each envelope is assigned to the protected or reference group based on
    ``group_attribute`` read from its ``semantic_payload``. Within each
    group, the *selection rate* is the fraction whose
    ``advancement_predicate`` is True. The ratio is
    ``rate_protected / rate_reference``; per the US EEOC four-fifths rule,
    ratios below 0.8 indicate disparate impact (``passed=False``).

    Envelopes whose ``group_attribute`` is missing or matches neither value
    are excluded.
    """
    protected_total = 0
    protected_advanced = 0
    reference_total = 0
    reference_advanced = 0

    for env in envelopes:
        value = _read_group_attribute(env, group_attribute)
        advanced = bool(advancement_predicate(env))
        if value == protected_value:
            protected_total += 1
            if advanced:
                protected_advanced += 1
        elif value == reference_value:
            reference_total += 1
            if advanced:
                reference_advanced += 1

    rate_protected = protected_advanced / protected_total if protected_total else 0.0
    rate_reference = reference_advanced / reference_total if reference_total else 0.0
    ratio = (rate_protected / rate_reference) if rate_reference > 0 else 0.0
    passed = ratio >= 0.8

    return FourFifthsResult(
        group_attribute=group_attribute,
        protected_value=protected_value,
        reference_value=reference_value,
        protected_total=protected_total,
        protected_advanced=protected_advanced,
        reference_total=reference_total,
        reference_advanced=reference_advanced,
        selection_rate_protected=rate_protected,
        selection_rate_reference=rate_reference,
        ratio=ratio,
        passed=passed,
    )


__all__ = [
    "FeatureUsageCensus",
    "FourFifthsResult",
    "feature_usage_census",
    "four_fifths_ratio",
]
