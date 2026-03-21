"""B2: SQLite persistence overhead — direct storage operations."""

from __future__ import annotations

import tempfile

from jhcontext.models import Decision

from .config import ITERATIONS, WARMUP
from .helpers import timed, fresh_storage, build_healthcare_envelope, build_education_envelope


def run() -> dict:
    print("  [B2] SQLite persistence...")

    results: dict = {}
    env_h, prov_h = build_healthcare_envelope()
    env_e, prov_g, prov_eq = build_education_envelope()
    turtle_h = prov_h.serialize("turtle")
    digest_h = prov_h.digest()
    turtle_g = prov_g.serialize("turtle")
    digest_g = prov_g.digest()

    # Single storage instance (INSERT OR REPLACE handles repeated writes)
    tmp = tempfile.mkdtemp(prefix="jhctx_b2_")
    s = fresh_storage(tmp)

    # Save envelope
    results["save_envelope"] = timed(
        lambda: s.save_envelope(env_h), ITERATIONS, WARMUP
    )

    # Get envelope
    results["get_envelope"] = timed(
        lambda: s.get_envelope(env_h.context_id), ITERATIONS, WARMUP
    )

    # Save PROV graph
    results["save_prov_graph"] = timed(
        lambda: s.save_prov_graph(env_h.context_id, turtle_h, digest_h), ITERATIONS, WARMUP
    )

    # Get PROV graph
    results["get_prov_graph"] = timed(
        lambda: s.get_prov_graph(env_h.context_id), ITERATIONS, WARMUP
    )

    # Save decision
    dec = Decision(context_id=env_h.context_id, outcome={"action": "approve"}, agent_id="agent-1")
    results["save_decision"] = timed(
        lambda: s.save_decision(dec), ITERATIONS, WARMUP
    )

    # Full healthcare persist (save envelope + prov, then retrieve both)
    def full_healthcare_persist():
        s.save_envelope(env_h)
        s.save_prov_graph(env_h.context_id, turtle_h, digest_h)
        s.get_envelope(env_h.context_id)
        s.get_prov_graph(env_h.context_id)

    results["full_healthcare_persist"] = timed(full_healthcare_persist, ITERATIONS, WARMUP)

    # Full education persist
    def full_education_persist():
        s.save_envelope(env_e)
        s.save_prov_graph(env_e.context_id, turtle_g, digest_g)
        s.get_envelope(env_e.context_id)
        s.get_prov_graph(env_e.context_id)

    results["full_education_persist"] = timed(full_education_persist, ITERATIONS, WARMUP)

    s.close()
    return results
