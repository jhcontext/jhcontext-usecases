"""Finance Scenario: Credit Assessment with full compliance pattern stack.

EU AI Act Annex III §5(b) high-risk; GDPR Arts. 13(2)(f), 14(2)(g), 15(1)(h),
17, 22, 25.

Pipeline: Data Collector → Risk Analyzer → Decision Agent
          + Senior Credit Officer (10-minute oversight review)
          + Fair Lending Agent (isolated workflow)

Combines all four PAC-AI compliance patterns:
  1. Negative proof — protected attributes (gender, ethnicity, marital_status,
     nationality) structurally absent from the decision chain (Art. 26(4)).
  2. Temporal oversight — credit officer review timestamped after AI
     recommendation, total duration ≥ 600 s (Art. 14).
  3. Workflow isolation — fair-lending PROV graph shares zero entities with
     the credit pipeline (verify_workflow_isolation).
  4. PII detachment — financial identifiers (tax ID, account numbers)
     tokenised before AI processing (GDPR Arts. 17, 25).

Principle 3 (recorded artifact = used artifact): the risk analyzer emits both
a raw credit-score embedding and a semantic risk assessment; the decision
agent consumes the semantic one, recorded via passed_artifact_pointer.

Outputs:
  - output/finance_envelope.json        (decision-stage JSON-LD envelope)
  - output/finance_credit_prov.ttl      (W3C PROV — credit pipeline)
  - output/finance_fair_lending_prov.ttl (W3C PROV — isolated fair-lending)
  - output/finance_audit.json           (audit report)
  - output/finance_metrics.json         (performance metrics)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jhcontext import (
    AbstractionLevel,
    ArtifactType,
    EnvelopeBuilder,
    PROVGraph,
    RiskLevel,
    TemporalScope,
    application,
    generate_audit_report,
    interpretation,
    observation,
    situation,
    verify_integrity,
    verify_negative_proof,
    verify_pii_detachment,
    verify_temporal_oversight,
    verify_workflow_isolation,
)
from jhcontext.pii import InMemoryPIIVault

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def run() -> dict:
    """Execute the finance credit-assessment scenario. Returns metrics dict."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics: dict = {}
    t_start = time.perf_counter()

    # --- Timestamps ---
    base = datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc)
    ts = {
        "collect_start": base,
        "collect_end": base + timedelta(seconds=30),
        "risk_start": base + timedelta(minutes=1),
        "risk_end": base + timedelta(minutes=2, seconds=30),
        "decide_start": base + timedelta(minutes=3),
        "decide_end": base + timedelta(minutes=4),
        # Senior credit officer's 10-minute review (paper §4)
        "officer_start": base + timedelta(minutes=5),
        "review_income_end": base + timedelta(minutes=8),
        "review_employment_end": base + timedelta(minutes=11),
        "review_bureau_end": base + timedelta(minutes=13),
        "review_ai_end": base + timedelta(minutes=15),
        "officer_signoff": base + timedelta(minutes=15, seconds=30),
        # Fair-lending workflow runs separately, on a different schedule
        "fair_lending_start": base + timedelta(hours=4),
        "fair_lending_end": base + timedelta(hours=4, minutes=20),
    }

    # =========================================================================
    # CREDIT PIPELINE — PROV graph
    # =========================================================================
    prov_credit = PROVGraph("ctx-finance-credit-001")

    # =========================================================================
    # STEP 1: Data Collector Agent — financial variables only
    # =========================================================================
    t0 = time.perf_counter()

    collector_payload = [
        # Financial identifiers — keyed so feature_suppression catches them
        # via field-name matching during PII detachment.
        {
            "@model": "Identifiers",
            "subject": "APP-2026-00847",
            "tax_id": "ES-X1234567Z",
            "account_number": "ES76-2100-0418-4012-3456-7891",
        },
        observation("APP-2026-00847", "monthly_gross_income", 5200,
                    range_="EUR-non-negative"),
        observation("APP-2026-00847", "employment_tenure_months", 48,
                    range_="non-negative-integer"),
        observation("APP-2026-00847", "monthly_debt_obligations", 1664,
                    range_="EUR-non-negative"),
        observation("APP-2026-00847", "credit_bureau_score", 718,
                    range_="bureau-score-300-850"),
        observation("APP-2026-00847", "on_time_payment_pct", 96,
                    range_="percentage-0-100"),
    ]

    builder = (
        EnvelopeBuilder()
        .set_producer("did:bank:data-collector-agent")
        .set_scope("credit_assessment")
        .set_ttl("PT2H")
        .set_risk_level(RiskLevel.HIGH)
        .set_human_oversight(True)
        .set_semantic_payload(list(collector_payload))
        .add_artifact(
            artifact_id="art-financial-data",
            artifact_type=ArtifactType.TOKEN_SEQUENCE,
            content_hash="sha256:" + "11" * 32,
        )
    )

    prov_credit.add_agent("data-collector-agent", "Financial Data Collector",
                          role="data_collector")
    prov_credit.add_entity("art-financial-data", "Verified Financial Inputs",
                           artifact_type="token_sequence",
                           content_hash="sha256:11...")
    prov_credit.add_activity("data-collection", "Financial Data Collection",
                             started_at=_iso(ts["collect_start"]),
                             ended_at=_iso(ts["collect_end"]),
                             method="EHR-equivalent banking integration")
    prov_credit.was_generated_by("art-financial-data", "data-collection")
    prov_credit.was_associated_with("data-collection", "data-collector-agent")

    metrics["collect_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 2: Risk Analyzer Agent — emits BOTH a raw embedding AND a semantic
    # risk assessment. Principle 3: passed_artifact_pointer records that the
    # decision agent consumed the semantic one (not the raw embedding).
    # =========================================================================
    t0 = time.perf_counter()

    risk_payload = list(collector_payload) + [
        # DTI = 1664 / 5200 ≈ 0.32 (32%) — paper §4
        interpretation("APP-2026-00847", "debt_to_income_ratio", 0.32,
                       range_="ratio-0-1", confidence=0.95),
        interpretation("APP-2026-00847", "payment_reliability", "excellent",
                       range_="ReliabilityEnum", confidence=0.96),
        interpretation("APP-2026-00847", "employment_stability", "stable",
                       range_="StabilityEnum", confidence=0.94),
        interpretation("APP-2026-00847", "default_probability", 0.034,
                       range_="probability-0-1", confidence=0.91),
        situation("APP-2026-00847", "creditworthy",
                  range_="CreditStatusEnum",
                  start=_iso(ts["risk_end"]),
                  confidence=0.93),
    ]
    builder.set_semantic_payload(risk_payload)

    # Raw embedding (NOT consumed downstream — Principle 3)
    builder.add_artifact(
        artifact_id="art-credit-score-embedding",
        artifact_type=ArtifactType.EMBEDDING,
        content_hash="sha256:" + "22" * 32,
        model="credit-encoder-v1",
        dimensions=768,
    )
    # Semantic risk assessment (this is what the decision agent consumes)
    builder.add_artifact(
        artifact_id="art-risk-analysis",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:" + "33" * 32,
        model="risk-analyzer-v2",
        confidence=0.93,
    )
    builder.set_passed_artifact("art-risk-analysis")

    prov_credit.add_agent("risk-analyzer-agent", "Credit Risk Analyzer",
                          role="risk_assessor")
    prov_credit.add_entity("art-credit-score-embedding",
                           "Raw Credit Score Embedding",
                           artifact_type="embedding",
                           content_hash="sha256:22...")
    prov_credit.add_entity("art-risk-analysis",
                           "Semantic Risk Assessment",
                           artifact_type="semantic_extraction",
                           content_hash="sha256:33...")
    prov_credit.add_activity("risk-analysis", "Credit Risk Analysis",
                             started_at=_iso(ts["risk_start"]),
                             ended_at=_iso(ts["risk_end"]),
                             method="LLM inference (risk-analyzer-v2)")
    # Both artifacts derive from the financial-data inputs only
    prov_credit.used("risk-analysis", "art-financial-data")
    prov_credit.was_generated_by("art-credit-score-embedding", "risk-analysis")
    prov_credit.was_generated_by("art-risk-analysis", "risk-analysis")
    prov_credit.was_associated_with("risk-analysis", "risk-analyzer-agent")
    prov_credit.was_derived_from("art-credit-score-embedding",
                                 "art-financial-data")
    prov_credit.was_derived_from("art-risk-analysis", "art-financial-data")

    metrics["risk_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 3: Decision Agent — consumes ONLY the semantic risk assessment
    # (Principle 3 / Semantic-Forward at high-risk tier).
    # =========================================================================
    t0 = time.perf_counter()

    decision_payload = list(risk_payload) + [
        application("APP-2026-00847", "credit_decision", "conditional_approval",
                    range_="DecisionEnum"),
        application("APP-2026-00847", "approved_amount",
                    {"value": 25000, "currency": "EUR"}),
        application("APP-2026-00847", "interest_rate",
                    {"annual_pct": 6.9, "type": "fixed"}),
        application("APP-2026-00847", "explanation_factors", [
            "POSITIVE — Bureau score 718 reflects solid repayment history.",
            "POSITIVE — On-time payments at 96% indicate excellent reliability.",
            "POSITIVE — Stable employment 48 months with permanent contract.",
            "POSITIVE — DTI ratio 32% (1,664 / 5,200 EUR) within prudent threshold.",
            "POSITIVE — Default probability 3.4% well below internal cutoff.",
            "CONDITION — High-risk classification mandates senior officer review (Art. 14).",
            "CONDITION — Approved amount capped at EUR 25,000 against requested EUR 30,000.",
        ]),
    ]
    builder.set_semantic_payload(decision_payload)

    builder.add_artifact(
        artifact_id="art-credit-decision",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:" + "44" * 32,
        model="credit-decision-v3",
        confidence=0.92,
    )
    builder.add_decision_influence(
        agent="credit-decision-agent",
        categories=["bureau_score", "payment_history", "employment_stability",
                    "debt_to_income", "default_probability"],
        influence_weights={
            "bureau_score": 0.30,
            "payment_history": 0.25,
            "employment_stability": 0.20,
            "debt_to_income": 0.18,
            "default_probability": 0.07,
        },
        confidence=0.92,
        abstraction_level=AbstractionLevel.SITUATION,
        temporal_scope=TemporalScope.CURRENT,
    )

    prov_credit.add_agent("credit-decision-agent", "Credit Decision Agent",
                          role="decision_maker")
    prov_credit.add_entity("art-credit-decision",
                           "AI Credit Decision",
                           artifact_type="semantic_extraction",
                           content_hash="sha256:44...")
    prov_credit.add_activity("ai-credit-decision", "AI Credit Decision",
                             started_at=_iso(ts["decide_start"]),
                             ended_at=_iso(ts["decide_end"]),
                             method="LLM inference (credit-decision-v3)")
    # Decision agent consumes ONLY the semantic risk analysis (Principle 3)
    prov_credit.used("ai-credit-decision", "art-risk-analysis")
    prov_credit.was_generated_by("art-credit-decision", "ai-credit-decision")
    prov_credit.was_associated_with("ai-credit-decision", "credit-decision-agent")
    prov_credit.was_derived_from("art-credit-decision", "art-risk-analysis")

    metrics["decide_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 4: Senior Credit Officer — 10-minute (600 s) review with
    # source-document access timestamped after the AI decision.
    # =========================================================================
    t0 = time.perf_counter()

    prov_credit.add_agent("officer-rivera", "Senior Credit Officer (Rivera)",
                          role="human_oversight")

    prov_credit.add_activity("review-income",
                             "Officer reviews income documentation",
                             started_at=_iso(ts["officer_start"]),
                             ended_at=_iso(ts["review_income_end"]))
    prov_credit.used("review-income", "art-financial-data")
    prov_credit.was_associated_with("review-income", "officer-rivera")

    prov_credit.add_activity("review-employment",
                             "Officer reviews employment verification",
                             started_at=_iso(ts["review_income_end"]),
                             ended_at=_iso(ts["review_employment_end"]))
    prov_credit.used("review-employment", "art-financial-data")
    prov_credit.was_associated_with("review-employment", "officer-rivera")

    prov_credit.add_activity("review-bureau",
                             "Officer reviews bureau report",
                             started_at=_iso(ts["review_employment_end"]),
                             ended_at=_iso(ts["review_bureau_end"]))
    prov_credit.used("review-bureau", "art-financial-data")
    prov_credit.was_associated_with("review-bureau", "officer-rivera")

    prov_credit.add_activity("review-ai-decision",
                             "Officer reviews AI credit decision",
                             started_at=_iso(ts["review_bureau_end"]),
                             ended_at=_iso(ts["review_ai_end"]))
    prov_credit.used("review-ai-decision", "art-credit-decision")
    prov_credit.was_associated_with("review-ai-decision", "officer-rivera")

    prov_credit.add_entity("art-officer-signoff",
                           "Senior Officer Approval",
                           artifact_type="semantic_extraction",
                           generated_at=_iso(ts["officer_signoff"]))
    prov_credit.add_activity("officer-signoff",
                             "Senior Officer Approval Decision",
                             started_at=_iso(ts["review_ai_end"]),
                             ended_at=_iso(ts["officer_signoff"]),
                             method="manual_review")
    prov_credit.was_generated_by("art-officer-signoff", "officer-signoff")
    prov_credit.was_associated_with("officer-signoff", "officer-rivera")
    prov_credit.was_derived_from("art-officer-signoff", "art-credit-decision")
    prov_credit.was_informed_by("officer-signoff", "review-income")
    prov_credit.was_informed_by("officer-signoff", "review-employment")
    prov_credit.was_informed_by("officer-signoff", "review-bureau")
    prov_credit.was_informed_by("officer-signoff", "review-ai-decision")

    builder.add_artifact(
        artifact_id="art-officer-signoff",
        artifact_type=ArtifactType.SEMANTIC_EXTRACTION,
        content_hash="sha256:" + "55" * 32,
        confidence=1.0,
    )

    metrics["officer_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 5: Fair-Lending Workflow — completely isolated from the credit
    # pipeline. Operates on aggregate cohort data with protected attributes;
    # shares zero PROV entities with the credit pipeline.
    # =========================================================================
    t0 = time.perf_counter()

    prov_fair = PROVGraph("ctx-finance-fair-lending-001")
    prov_fair.add_agent("fair-lending-agent", "Fair-Lending Compliance Agent",
                        role="compliance_reporter")
    # Aggregate cohort data carrying protected attributes lives ONLY here
    prov_fair.add_entity("art-cohort-aggregates",
                         "Aggregate Cohort Demographics",
                         artifact_type="sensitive",
                         content_hash="sha256:" + "66" * 32)
    prov_fair.add_entity("art-disparity-statistics",
                         "Disparity Test Statistics",
                         artifact_type="semantic_extraction",
                         content_hash="sha256:" + "77" * 32)
    prov_fair.add_entity("art-fair-lending-report",
                         "Quarterly Fair-Lending Report",
                         artifact_type="semantic_extraction",
                         content_hash="sha256:" + "88" * 32)
    prov_fair.add_activity("fair-lending-analysis",
                           "Aggregate disparity analysis",
                           started_at=_iso(ts["fair_lending_start"]),
                           ended_at=_iso(ts["fair_lending_end"]),
                           method="four_fifths_rule_aggregation")
    prov_fair.used("fair-lending-analysis", "art-cohort-aggregates")
    prov_fair.was_generated_by("art-disparity-statistics",
                               "fair-lending-analysis")
    prov_fair.was_generated_by("art-fair-lending-report",
                               "fair-lending-analysis")
    prov_fair.was_associated_with("fair-lending-analysis",
                                  "fair-lending-agent")
    prov_fair.was_derived_from("art-disparity-statistics",
                               "art-cohort-aggregates")
    prov_fair.was_derived_from("art-fair-lending-report",
                               "art-disparity-statistics")

    metrics["fair_lending_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 6: Build, sign, and finalize envelope (with PII detachment).
    # =========================================================================
    t0 = time.perf_counter()

    pii_vault = InMemoryPIIVault()

    builder.set_privacy(
        data_category="behavioral",
        legal_basis="contract",  # GDPR Art. 6(1)(b) — necessary for contract
        retention="P10Y",        # banking record-keeping requirement
        storage_policy="bank-encrypted",
        feature_suppression=["tax_id", "account_number"],
    )
    builder.set_compliance(
        risk_level=RiskLevel.HIGH,
        human_oversight_required=True,
        model_card_ref="https://bank.example/models/credit-decision-v3",
        test_suite_ref="https://bank.example/fair-lending-tests/2026-Q2",
        escalation_path="credit-risk-committee@bank.example",
    )

    # Tokenise tax_id + account_number before signing
    builder.enable_pii_detachment(vault=pii_vault)

    # Attach PROV reference BEFORE signing
    prov_digest = prov_credit.digest()
    builder._envelope.provenance_ref.prov_graph_id = f"prov:{prov_credit.context_id}"
    builder._envelope.provenance_ref.prov_digest = prov_digest

    envelope = builder.sign("did:bank:compliance-officer").build()

    metrics["envelope_build_ms"] = (time.perf_counter() - t0) * 1000

    # =========================================================================
    # STEP 7: Audit — all four PAC-AI compliance patterns
    # =========================================================================
    t0 = time.perf_counter()

    # 7a. Negative proof: protected attributes structurally absent from credit chain
    negative_result = verify_negative_proof(
        prov_credit,
        decision_entity_id="art-credit-decision",
        excluded_artifact_types=[
            "gender", "ethnicity", "marital_status", "nationality",
        ],
    )

    # 7b. Workflow isolation: credit pipeline ⊥ fair-lending workflow
    isolation_result = verify_workflow_isolation(prov_credit, prov_fair)

    # 7c. Temporal oversight: officer review after AI decision, ≥600 s
    temporal_result = verify_temporal_oversight(
        prov_credit,
        ai_activity_id="ai-credit-decision",
        human_activities=[
            "review-income",
            "review-employment",
            "review-bureau",
            "review-ai-decision",
        ],
        min_review_seconds=600.0,  # 10 minutes (paper §4)
    )

    # 7d. PII detachment
    pii_result = verify_pii_detachment(envelope)

    # 7e. Integrity (Ed25519 + URDNA2015)
    integrity_result = verify_integrity(envelope)

    report = generate_audit_report(
        envelope, prov_credit,
        [negative_result, isolation_result, temporal_result,
         pii_result, integrity_result],
    )

    # 7f. GDPR Art. 17 erasure: purge tokens, integrity must still hold
    pii_purged = pii_vault.purge_by_context(envelope.context_id)
    post_purge_integrity = verify_integrity(envelope)
    metrics["pii_tokens_purged"] = pii_purged
    metrics["integrity_after_purge"] = post_purge_integrity.passed

    metrics["audit_ms"] = (time.perf_counter() - t0) * 1000
    metrics["total_ms"] = (time.perf_counter() - t_start) * 1000

    # =========================================================================
    # STEP 8: Save outputs
    # =========================================================================
    envelope_path = OUTPUT_DIR / "finance_envelope.json"
    envelope_path.write_text(
        json.dumps(envelope.to_jsonld(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    credit_prov_path = OUTPUT_DIR / "finance_credit_prov.ttl"
    credit_prov_path.write_text(prov_credit.serialize("turtle"),
                                encoding="utf-8")

    fair_prov_path = OUTPUT_DIR / "finance_fair_lending_prov.ttl"
    fair_prov_path.write_text(prov_fair.serialize("turtle"), encoding="utf-8")

    audit_path = OUTPUT_DIR / "finance_audit.json"
    audit_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics["credit_prov_entities"] = len(prov_credit.get_all_entities())
    metrics["fair_lending_prov_entities"] = len(prov_fair.get_all_entities())
    metrics["envelope_size_bytes"] = envelope_path.stat().st_size

    metrics_path = OUTPUT_DIR / "finance_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    # =========================================================================
    # Console summary
    # =========================================================================
    print("=" * 60)
    print("FINANCE SCENARIO — Annex III §5(b) credit assessment")
    print("=" * 60)
    print(f"  Context ID:          {envelope.context_id}")
    print(f"  Producer:            {envelope.producer}")
    print(f"  Risk Level:          {envelope.compliance.risk_level.value}")
    print(f"  Forwarding Policy:   {envelope.compliance.forwarding_policy.value}")
    print(f"  Schema Version:      {envelope.schema_version}")
    print(f"  Canonicalization:    {envelope.proof.canonicalization}")
    print(f"  Passed Artifact:     {envelope.passed_artifact_pointer}")
    print(f"  Credit-PROV Entities:    {metrics['credit_prov_entities']}")
    print(f"  Fair-Lending Entities:   {metrics['fair_lending_prov_entities']}")
    print()
    print("  AUDIT RESULTS:")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"    [{status}] {r.check_name}: {r.message}")
    print(f"  Overall: {'PASSED' if report.overall_passed else 'FAILED'}")
    print()
    print(f"  PII Detachment:           {envelope.privacy.pii_detached}")
    print(f"  Suppressed fields:        {envelope.privacy.feature_suppression}")
    print(f"  PII Tokens Purged (GDPR Art. 17): {metrics['pii_tokens_purged']}")
    print(f"  Integrity After Purge:    {metrics['integrity_after_purge']}")
    print()
    print(f"  Outputs saved to: {OUTPUT_DIR}/")
    print("=" * 60)

    return metrics


if __name__ == "__main__":
    run()
