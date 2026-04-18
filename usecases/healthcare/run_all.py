"""Run all three offline healthcare scenarios end-to-end.

Usage:
    python -m usecases.healthcare.run_all
"""

from __future__ import annotations

import json
from pathlib import Path

from usecases.healthcare.chronic_monitoring.run import run as run_chronic
from usecases.healthcare.chw_mental_health.run import run as run_chw
from usecases.healthcare.triage_rural.run import run as run_triage

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"


def main() -> None:
    print("\n>>> Healthcare offline scenarios: running all three >>>\n")
    m1 = run_triage()
    print()
    m2 = run_chronic()
    print()
    m3 = run_chw()

    summary = {
        "scenario_1_triage_rural": m1,
        "scenario_2_chronic_monitoring": m2,
        "scenario_3_chw_mental_health": m3,
    }
    summary_path = OUTPUT_DIR / "healthcare_scenarios_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print("All three healthcare scenarios completed:")
    print(f"  Scenario 1 (triage):       {m1['total_ms']:.1f} ms")
    print(f"  Scenario 2 (monitoring):   {m2['total_ms']:.1f} ms")
    print(f"  Scenario 3 (CHW mh):       {m3['total_ms']:.1f} ms")
    print(f"  Summary: {summary_path}")
    print("=" * 64)


if __name__ == "__main__":
    main()
