"""Benchmark suite entry point.

Usage:
    python -m usecases.benchmarks.run [--iterations N] [--no-figures]
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config
from . import bench_inmemory
from . import bench_storage
from . import bench_api
from . import bench_mcp
from . import bench_prov_scaling
from . import bench_crypto
from . import bench_compliance
from . import report


def main():
    parser = argparse.ArgumentParser(description="PAC-AI benchmark suite")
    parser.add_argument("--iterations", "-n", type=int, default=None,
                        help=f"Override iterations (default: {config.ITERATIONS})")
    parser.add_argument("--no-figures", action="store_true",
                        help="Skip matplotlib figure generation")
    args = parser.parse_args()

    if args.iterations:
        config.ITERATIONS = args.iterations

    print("=" * 60)
    print("PAC-AI BENCHMARK SUITE")
    print(f"  Iterations: {config.ITERATIONS} (+ {config.WARMUP} warmup)")
    print("=" * 60)

    all_results: dict = {}
    t_start = time.perf_counter()

    # B1
    all_results["inmemory"] = bench_inmemory.run()

    # B2
    all_results["storage"] = bench_storage.run()

    # B3
    all_results["api"] = bench_api.run()

    # B4
    try:
        all_results["mcp"] = bench_mcp.run()
    except Exception as e:
        print(f"  [B4] MCP bench failed: {e}")
        all_results["mcp"] = {"error": str(e)}

    # B5
    all_results["prov_scaling"] = bench_prov_scaling.run()

    # B6
    all_results["crypto"] = bench_crypto.run()

    # B7
    all_results["compliance"] = bench_compliance.run()

    total = (time.perf_counter() - t_start)
    print(f"\nAll benchmarks completed in {total:.1f}s")
    print()

    # Generate report
    report.generate(all_results)


if __name__ == "__main__":
    main()
