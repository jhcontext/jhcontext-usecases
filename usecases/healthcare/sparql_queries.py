"""AIiH SPARQL audit queries over a triage-cohort envelope.

Runs the two queries shown in the AIiH paper:

  1. LOW_CONFIDENCE_AF_TRIAGE — every patient with a suspected AF finding
     produced below a confidence threshold — the population routed to
     mandatory clinician review before triage commit (Art. 14 oversight).

  2. NEGATIVE_PROOF_IDENTITY — verify that no statement in the triage
     chain carries a suppressed attribute (insurance_tier, demographic).
     Returns zero rows iff negative proof holds (Art. 13 transparency).

Usage:
    python -m usecases.healthcare.sparql_queries
"""

from __future__ import annotations

from pathlib import Path

from usecases._sparql import load_envelope_graph, run_query, print_table


ENVELOPE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "output" / "healthcare_triage_envelope.json"
)


LOW_CONFIDENCE_AF_TRIAGE = """
# Every patient whose suspected AF finding (SNOMED 49436004) was produced
# below 0.80 confidence — the population routed to mandatory clinician
# review before triage commit.
SELECT ?patient ?confidence ?creator
WHERE {
  ?stmt jh:mainpart       ?main ;
        jh:explanation    ?exp ;
        jh:administration ?adm .
  ?main jh:subject        ?patient ;
        jh:auxiliary      "hasFinding" ;
        jh:object         "snomed:49436004" .
  ?exp  jh:confidence     ?confidence ;
        jh:creator        ?creator ;
        jh:finding_status "suspected" .
  ?adm  jh:group          "Interpretation" .

  FILTER (?confidence < 0.80)
}
ORDER BY ?confidence
"""


NEGATIVE_PROOF_IDENTITY = """
# Negative proof over this envelope: no statement in the chain may consume
# insurance-tier or demographic attributes. Returns zero rows iff verified.
SELECT ?subject ?predicate ?group
WHERE {
  ?stmt jh:mainpart       ?main ;
        jh:administration ?adm .
  ?main jh:subject        ?subject ;
        jh:predicate      ?predicate .
  ?adm  jh:group          ?group .

  FILTER (?predicate IN ("insurance_tier", "insurance_status",
                         "demographic_group", "demographic",
                         "ethnic_group", "postal_code"))
}
"""


def main() -> None:
    if not ENVELOPE_PATH.exists():
        raise SystemExit(
            f"Envelope not found at {ENVELOPE_PATH}. "
            "Run `python -m usecases.healthcare.scenario_triage` first."
        )
    g = load_envelope_graph(ENVELOPE_PATH)

    print("=" * 72)
    print("AIiH Triage Scenario — SPARQL Audit Queries")
    print(f"Envelope: {ENVELOPE_PATH.name} ({len(g)} triples)")
    print("=" * 72)

    print("\n[Query 1] Suspected-AF patients below 0.80 confidence "
          "(Art. 14 review queue)\n")
    rows = run_query(g, LOW_CONFIDENCE_AF_TRIAGE)
    print_table(rows, ["patient", "confidence", "creator"])

    print("\n[Query 2] Negative proof for suppressed identity attributes "
          "(0 rows = verified)\n")
    rows = run_query(g, NEGATIVE_PROOF_IDENTITY)
    print_table(rows, ["subject", "predicate", "group"])


if __name__ == "__main__":
    main()
