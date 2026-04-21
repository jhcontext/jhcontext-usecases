"""AIET SPARQL audit queries over a Scenario B feedback envelope.

Runs the two queries shown in the AIET paper:

  1. RUBRIC_CRITERION_AUDIT — for each feedback sentence that claims to
     address a given rubric criterion, return its cited evidence span,
     confidence, agent, and prompt-template. Sorted low-confidence first.

  2. ORPHAN_SENTENCE_DETECTION — flag any Interpretation-group statement
     that binds a sentence to a rubric criterion without an evidence span.
     Returns zero rows when the envelope is well-formed.

Usage:
    python -m usecases.education.sparql_queries
"""

from __future__ import annotations

from pathlib import Path

from usecases._sparql import load_envelope_graph, run_query, print_table


ENVELOPE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "output" / "education_scenario_b_envelope.json"
)


# -----------------------------------------------------------------------------
# Query 1 — Rubric-criterion audit (student- or institutional-facing)
# -----------------------------------------------------------------------------

RUBRIC_CRITERION_AUDIT = """
# Show every feedback sentence that claims to address a specific rubric
# criterion, with the cited evidence span, confidence, agent, and
# prompt-template. Sorted by confidence ascending so the lowest-confidence
# (and most review-worthy) claims appear first.
SELECT ?sentence ?criterion ?confidence ?creator ?method ?offset ?length ?hash
WHERE {
  ?stmt jh:mainpart       ?main ;
        jh:explanation    ?exp ;
        jh:administration ?adm .
  ?main jh:subject        ?sentence ;
        jh:auxiliary      "addresses" ;
        jh:predicate      "rubric_criterion" ;
        jh:object         ?criterion .
  ?adm  jh:group          "Interpretation" .
  ?exp  jh:confidence     ?confidence ;
        jh:creator        ?creator ;
        jh:method         ?method ;
        jh:evidence       ?ev .
  ?ev   jh:offset         ?offset ;
        jh:length         ?length ;
        jh:hash           ?hash .
}
ORDER BY ASC(?confidence)
"""


# -----------------------------------------------------------------------------
# Query 2 — Orphan-sentence structural verifier
# -----------------------------------------------------------------------------

ORPHAN_SENTENCE_DETECTION = """
# Flag any Interpretation-group statement binding a sentence to a rubric
# criterion but with NO evidence span in its explanation box. Zero rows
# means the envelope satisfies structural rubric-grounding.
SELECT ?sentence ?criterion
WHERE {
  ?stmt jh:mainpart       ?main ;
        jh:administration ?adm .
  ?main jh:subject        ?sentence ;
        jh:auxiliary      "addresses" ;
        jh:predicate      "rubric_criterion" ;
        jh:object         ?criterion .
  ?adm  jh:group          "Interpretation" .

  FILTER NOT EXISTS {
    ?stmt jh:explanation  ?exp .
    ?exp  jh:evidence     ?ev .
  }
}
"""


def main() -> None:
    if not ENVELOPE_PATH.exists():
        raise SystemExit(
            f"Envelope not found at {ENVELOPE_PATH}. "
            "Run `python -m usecases.education.scenario_b` first."
        )
    g = load_envelope_graph(ENVELOPE_PATH)

    print("=" * 72)
    print("AIET Scenario B — SPARQL Audit Queries")
    print(f"Envelope: {ENVELOPE_PATH.name} ({len(g)} triples)")
    print("=" * 72)

    print("\n[Query 1] Rubric-criterion audit (sorted by confidence ASC)\n")
    rows = run_query(g, RUBRIC_CRITERION_AUDIT)
    print_table(rows, ["sentence", "criterion", "confidence", "creator", "method",
                       "offset", "length", "hash"])

    print("\n[Query 2] Orphan-sentence structural verifier "
          "(0 rows = verifier passes)\n")
    rows = run_query(g, ORPHAN_SENTENCE_DETECTION)
    print_table(rows, ["sentence", "criterion"])


if __name__ == "__main__":
    main()
