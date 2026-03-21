"""Report generation: ASCII tables, JSON, CSV, optional matplotlib figures."""

from __future__ import annotations

import csv
import io
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROV_SIZES

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "output" / "benchmarks"


def _fmt(v: float) -> str:
    """Format milliseconds nicely."""
    if v < 0.01:
        return "<0.01"
    if v < 1:
        return f"{v:.3f}"
    if v < 100:
        return f"{v:.2f}"
    return f"{v:.1f}"


def _stat_row(label: str, stats: dict) -> str:
    return f"  {label:<35} {_fmt(stats['mean']):>8} {_fmt(stats['median']):>8} {_fmt(stats['std']):>8} {_fmt(stats['p95']):>8} {_fmt(stats['min']):>8} {_fmt(stats['max']):>8}"


def _header() -> str:
    return f"  {'Operation':<35} {'Mean':>8} {'Median':>8} {'StdDev':>8} {'P95':>8} {'Min':>8} {'Max':>8}"


def _sep() -> str:
    return "  " + "-" * 83


def generate(all_results: dict[str, dict]) -> None:
    """Generate all output files from benchmark results."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    metadata = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor(),
    }

    lines.append("=" * 87)
    lines.append("PAC-AI BENCHMARK RESULTS")
    lines.append(f"  Python: {sys.version.split()[0]}  |  Platform: {platform.platform()}")
    lines.append("=" * 87)

    # B1: In-Memory
    if "inmemory" in all_results:
        b1 = all_results["inmemory"]
        lines.append("")
        lines.append("B1: IN-MEMORY BASELINE (ms)")
        lines.append(_header())
        lines.append(_sep())
        for key in ["healthcare_build", "healthcare_audit", "education_build", "education_audit"]:
            if key in b1:
                lines.append(_stat_row(key, b1[key]))

    # B2: Storage
    if "storage" in all_results:
        b2 = all_results["storage"]
        lines.append("")
        lines.append("B2: SQLITE PERSISTENCE (ms)")
        lines.append(_header())
        lines.append(_sep())
        for key, stats in b2.items():
            if isinstance(stats, dict) and "mean" in stats:
                lines.append(_stat_row(key, stats))

    # B3: REST API
    if "api" in all_results:
        b3 = all_results["api"]
        lines.append("")
        lines.append("B3: REST API ROUND-TRIP (ms)")
        lines.append(_header())
        lines.append(_sep())
        for key, stats in b3.items():
            if isinstance(stats, dict) and "mean" in stats:
                lines.append(_stat_row(key, stats))

    # B4: MCP
    if "mcp" in all_results:
        b4 = all_results["mcp"]
        lines.append("")
        lines.append("B4: MCP TOOL DISPATCH (ms)")
        lines.append(_header())
        lines.append(_sep())
        for key, stats in b4.items():
            if isinstance(stats, dict) and "mean" in stats:
                lines.append(_stat_row(key, stats))

    # B5: PROV Scaling
    if "prov_scaling" in all_results:
        b5 = all_results["prov_scaling"]
        lines.append("")
        lines.append("B5: PROV GRAPH SCALING")
        lines.append(f"  {'Entities':>8} {'Build':>10} {'Serialize':>10} {'Digest':>10} {'Causal':>10} {'Temporal':>10} {'DepChain':>10} {'AllEnts':>10} {'Size(B)':>10}")
        lines.append("  " + "-" * 88)
        for n in PROV_SIZES:
            label = f"n{n}"
            def _g(k):
                v = b5.get(f"{label}_{k}")
                if isinstance(v, dict):
                    return _fmt(v["mean"])
                if isinstance(v, (int, float)):
                    return str(v)
                return "—"
            lines.append(
                f"  {n:>8} {_g('build'):>10} {_g('serialize'):>10} {_g('digest'):>10} "
                f"{_g('causal_chain'):>10} {_g('temporal_sequence'):>10} "
                f"{_g('entities_in_chain'):>10} {_g('all_entities'):>10} {_g('size_bytes'):>10}"
            )

    # B6: Crypto
    if "crypto" in all_results:
        b6 = all_results["crypto"]
        lines.append("")
        lines.append("B6: CRYPTO OVERHEAD (ms)")
        lines.append(_header())
        lines.append(_sep())
        for key, stats in b6.items():
            if isinstance(stats, dict) and "mean" in stats:
                lines.append(_stat_row(key, stats))

    # B7: Compliance
    if "compliance" in all_results:
        b7 = all_results["compliance"]
        lines.append("")
        lines.append("B7: COMPLIANCE PACKAGE EXPORT")
        if "export_time" in b7 and isinstance(b7["export_time"], dict):
            lines.append(_header())
            lines.append(_sep())
            lines.append(_stat_row("export_package", b7["export_time"]))
        lines.append(f"  ZIP size:          {b7.get('zip_size_bytes', '?')} bytes")
        lines.append(f"  All files present: {b7.get('all_files_present', '?')}")
        lines.append(f"  Audit passed:      {b7.get('audit_passed', '?')}")
        lines.append(f"  ZIP contents:      {b7.get('zip_contents', '?')}")

    # Comparison table
    if all(k in all_results for k in ["inmemory", "storage", "api"]):
        lines.append("")
        lines.append("COMPARISON: OPERATION LATENCY ACROSS LAYERS (mean ms)")
        lines.append(f"  {'Operation':<30} {'In-Memory':>10} {'+ SQLite':>10} {'REST API':>10} {'MCP':>10}")
        lines.append("  " + "-" * 70)

        b1 = all_results["inmemory"]
        b2 = all_results["storage"]
        b3 = all_results["api"]
        b4 = all_results.get("mcp", {})

        def _m(d, k):
            v = d.get(k, {})
            return _fmt(v["mean"]) if isinstance(v, dict) and "mean" in v else "—"

        lines.append(f"  {'Envelope build+sign':<30} {_m(b1,'healthcare_build'):>10} {'—':>10} {'—':>10} {'—':>10}")
        lines.append(f"  {'Envelope persist':<30} {'—':>10} {_m(b2,'save_envelope'):>10} {_m(b3,'post_envelope'):>10} {_m(b4,'submit_envelope'):>10}")
        lines.append(f"  {'Envelope retrieve':<30} {'—':>10} {_m(b2,'save_get_envelope'):>10} {_m(b3,'get_envelope'):>10} {_m(b4,'get_envelope'):>10}")
        lines.append(f"  {'PROV persist':<30} {'—':>10} {_m(b2,'save_prov_graph'):>10} {_m(b3,'post_provenance'):>10} {_m(b4,'submit_prov_graph'):>10}")
        lines.append(f"  {'PROV query (causal)':<30} {'—':>10} {'—':>10} {_m(b3,'query_causal_chain'):>10} {_m(b4,'query_causal_chain'):>10}")
        lines.append(f"  {'PROV query (temporal)':<30} {'—':>10} {'—':>10} {_m(b3,'query_temporal_sequence'):>10} {_m(b4,'query_temporal_sequence'):>10}")
        lines.append(f"  {'Audit (integrity)':<30} {'—':>10} {'—':>10} {'—':>10} {_m(b4,'run_audit'):>10}")
        lines.append(f"  {'Compliance package':<30} {'—':>10} {'—':>10} {_m(b3,'compliance_package'):>10} {'—':>10}")
        lines.append(f"  {'Full healthcare':<30} {_m(b1,'healthcare_build'):>10} {_m(b2,'full_healthcare_persist'):>10} {_m(b3,'full_healthcare_api'):>10} {'—':>10}")
        lines.append(f"  {'Audit (healthcare)':<30} {_m(b1,'healthcare_audit'):>10} {'—':>10} {'—':>10} {'—':>10}")

    lines.append("")
    lines.append("=" * 87)

    # Print
    output = "\n".join(lines)
    print(output)

    # Save summary.txt
    (OUTPUT_DIR / "summary.txt").write_text(output, encoding="utf-8")

    # Save results.json
    full_results = {"metadata": metadata, "benchmarks": all_results}
    (OUTPUT_DIR / "results.json").write_text(
        json.dumps(full_results, indent=2, default=str), encoding="utf-8"
    )

    # Save results.csv
    _write_csv(all_results)

    # Optional figures
    _try_figures(all_results)


def _write_csv(all_results: dict) -> None:
    """Write summary CSV for LaTeX import."""
    rows: list[dict] = []

    for bench_name, bench_data in all_results.items():
        if not isinstance(bench_data, dict):
            continue
        for op_name, stats in bench_data.items():
            if isinstance(stats, dict) and "mean" in stats:
                rows.append({
                    "benchmark": bench_name,
                    "operation": op_name,
                    "mean_ms": round(stats["mean"], 3),
                    "median_ms": round(stats["median"], 3),
                    "std_ms": round(stats["std"], 3),
                    "p95_ms": round(stats["p95"], 3),
                    "min_ms": round(stats["min"], 3),
                    "max_ms": round(stats["max"], 3),
                })

    if rows:
        path = OUTPUT_DIR / "results.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


def _try_figures(all_results: dict) -> None:
    """Generate matplotlib figures if available."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not available, skipping figures)")
        return

    fig_dir = OUTPUT_DIR / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    # Figure 1: Overhead comparison
    if all(k in all_results for k in ["storage", "api"]):
        _fig_overhead(all_results, fig_dir, plt)

    # Figure 2: PROV scaling
    if "prov_scaling" in all_results:
        _fig_prov_scaling(all_results["prov_scaling"], fig_dir, plt)

    # Figure 3: Crypto breakdown
    if "crypto" in all_results:
        _fig_crypto(all_results["crypto"], fig_dir, plt)


def _fig_overhead(all_results: dict, fig_dir: Path, plt) -> None:
    b2 = all_results["storage"]
    b3 = all_results["api"]
    b4 = all_results.get("mcp", {})

    ops = ["save_envelope", "save_prov_graph"]
    api_ops = ["post_envelope", "post_provenance"]
    mcp_ops = ["submit_envelope", "submit_prov_graph"]
    labels = ["Envelope Persist", "PROV Persist"]

    sqlite_vals = [b2.get(o, {}).get("mean", 0) for o in ops]
    api_vals = [b3.get(o, {}).get("mean", 0) for o in api_ops]
    mcp_vals = [b4.get(o, {}).get("mean", 0) for o in mcp_ops]

    x = range(len(labels))
    w = 0.25
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([i - w for i in x], sqlite_vals, w, label="SQLite Direct", color="#2196F3")
    ax.bar(list(x), api_vals, w, label="REST API", color="#FF9800")
    ax.bar([i + w for i in x], mcp_vals, w, label="MCP", color="#4CAF50")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("PAC-AI: Protocol Operation Latency by Interface")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(fig_dir / "overhead_comparison.png", dpi=150)
    plt.close(fig)
    print(f"  Saved {fig_dir / 'overhead_comparison.png'}")


def _fig_prov_scaling(b5: dict, fig_dir: Path, plt) -> None:
    queries = ["causal_chain", "temporal_sequence", "entities_in_chain", "all_entities"]
    query_labels = ["Causal Chain", "Temporal Seq", "Dep. Chain", "All Entities"]
    colors = ["#2196F3", "#FF9800", "#4CAF50", "#F44336"]

    fig, ax = plt.subplots(figsize=(8, 5))
    for query, label, color in zip(queries, query_labels, colors):
        means = []
        sizes = []
        for n in PROV_SIZES:
            key = f"n{n}_{query}"
            if key in b5 and isinstance(b5[key], dict):
                means.append(b5[key]["mean"])
                sizes.append(n)
        if means:
            ax.plot(sizes, means, "o-", label=label, color=color, linewidth=2, markersize=6)

    ax.set_xlabel("Number of Entities")
    ax.set_ylabel("Query Time (ms)")
    ax.set_title("PAC-AI: PROV Graph Query Scaling")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(fig_dir / "prov_scaling.png", dpi=150)
    plt.close(fig)
    print(f"  Saved {fig_dir / 'prov_scaling.png'}")


def _fig_crypto(b6: dict, fig_dir: Path, plt) -> None:
    ops = []
    vals = []
    for key in ["sha256_1024b", "sha256_10240b", "sha256_102400b",
                 "canonicalize", "content_hash", "sign_envelope", "verify_envelope", "prov_digest"]:
        if key in b6 and isinstance(b6[key], dict):
            ops.append(key.replace("_", "\n"))
            vals.append(b6[key]["mean"])

    if not ops:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#2196F3"] * 3 + ["#FF9800"] * 2 + ["#4CAF50"] * 2 + ["#F44336"]
    ax.bar(range(len(ops)), vals, color=colors[:len(ops)])
    ax.set_xticks(range(len(ops)))
    ax.set_xticklabels(ops, fontsize=8)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("PAC-AI: Cryptographic Operation Overhead")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(fig_dir / "crypto_breakdown.png", dpi=150)
    plt.close(fig)
    print(f"  Saved {fig_dir / 'crypto_breakdown.png'}")
