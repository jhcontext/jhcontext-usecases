"""B5: PROV graph scaling — query performance vs graph size."""

from __future__ import annotations

from .config import ITERATIONS, WARMUP, PROV_SIZES
from .helpers import timed, generate_prov_graph


def run() -> dict:
    print("  [B5] PROV scaling...")

    results: dict = {}

    for n in PROV_SIZES:
        print(f"    n={n}...")
        label = f"n{n}"

        # Build
        results[f"{label}_build"] = timed(lambda n=n: generate_prov_graph(n), ITERATIONS, WARMUP)

        # Pre-build for query benchmarks
        prov = generate_prov_graph(n)
        entities = prov.get_all_entities()
        leaf = entities[-1] if entities else "source-0"
        activities = prov.get_temporal_sequence()
        activity_id = activities[-1]["activity_id"] if activities else "activity-1"

        # Serialize
        results[f"{label}_serialize"] = timed(lambda: prov.serialize("turtle"), ITERATIONS, WARMUP)

        # Digest
        results[f"{label}_digest"] = timed(lambda: prov.digest(), ITERATIONS, WARMUP)

        # Queries
        results[f"{label}_causal_chain"] = timed(
            lambda l=leaf: prov.get_causal_chain(l), ITERATIONS, WARMUP
        )
        results[f"{label}_temporal_sequence"] = timed(
            lambda: prov.get_temporal_sequence(), ITERATIONS, WARMUP
        )
        results[f"{label}_entities_in_chain"] = timed(
            lambda l=leaf: prov.get_entities_in_chain(l), ITERATIONS, WARMUP
        )
        results[f"{label}_all_entities"] = timed(
            lambda: prov.get_all_entities(), ITERATIONS, WARMUP
        )

        # Graph size
        turtle = prov.serialize("turtle")
        results[f"{label}_size_bytes"] = len(turtle.encode("utf-8"))

    return results
