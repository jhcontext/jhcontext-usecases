"""Shared SPARQL runner — loads a PAC-AI envelope JSON-LD file and runs queries.

The SDK's `Envelope.to_jsonld()` emits a plain dict without an `@context`.
For rdflib to parse it as proper JSON-LD we inject a jhcontext vocabulary
context that maps every envelope property into the `https://jhcontext.com/vocab#`
namespace.

Usage:
    from usecases._sparql import load_envelope_graph, run_query

    g = load_envelope_graph("output/education_scenario_b_envelope.json")
    for row in run_query(g, QUERY_TEXT):
        print(row)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from rdflib import Graph
from rdflib.query import ResultRow

JH_CONTEXT = {
    "@vocab": "https://jhcontext.com/vocab#",
    "jh": "https://jhcontext.com/vocab#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

SPARQL_PREFIXES = """\
PREFIX jh:  <https://jhcontext.com/vocab#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""


def load_envelope_graph(envelope_path: str | Path) -> Graph:
    """Load a PAC-AI envelope JSON-LD file into an rdflib Graph.

    Merges a ``@vocab`` entry into whatever ``@context`` the envelope carries
    so that bare keys (``mainpart``, ``auxiliary``, ``predicate``, ``range``,
    ``object``, ``administration``, ``explanation``, ``semantic_payload``)
    resolve to IRIs in the jhcontext vocabulary. Adds a top-level ``@id``
    derived from ``context_id`` so every nested object becomes a connected
    blank node rather than an orphan.
    """
    data = json.loads(Path(envelope_path).read_text(encoding="utf-8"))

    existing_ctx = data.get("@context")
    if isinstance(existing_ctx, dict):
        existing_ctx.setdefault("@vocab", JH_CONTEXT["@vocab"])
    else:
        data["@context"] = dict(JH_CONTEXT)

    if "@id" not in data and "context_id" in data:
        data["@id"] = f"urn:envelope:{data['context_id']}"

    g = Graph()
    g.parse(data=json.dumps(data), format="json-ld")
    return g


def run_query(g: Graph, sparql: str) -> list[ResultRow]:
    """Run a SPARQL SELECT query, prefixing the jh: namespace automatically."""
    if "PREFIX jh:" not in sparql:
        sparql = SPARQL_PREFIXES + sparql
    return list(g.query(sparql))


def print_table(rows: Iterable[ResultRow], columns: list[str]) -> None:
    """Print SPARQL result rows as a fixed-column table."""
    widths = {c: max(len(c), 4) for c in columns}
    rows = list(rows)
    for row in rows:
        for c in columns:
            val = str(getattr(row, c, ""))
            widths[c] = max(widths[c], min(len(val), 60))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("-" * len(header))
    for row in rows:
        line = "  ".join(
            str(getattr(row, c, ""))[: widths[c]].ljust(widths[c]) for c in columns
        )
        print(line)
    print(f"({len(rows)} rows)")
