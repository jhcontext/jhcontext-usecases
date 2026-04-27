"""Run all three hiring scenarios in sequence and print a combined summary.

Default: every scenario uses its non-injected fixtures.
With ``--inject-violation``: every scenario seeds its specific violation.
"""

from __future__ import annotations

import sys

from usecases.hiring.procurement import run as procurement_run
from usecases.hiring.inflight_oversight import run as inflight_run
from usecases.hiring.cohort_audit import run as cohort_run


def _inject_flag(argv: list[str]) -> bool:
    return "--inject-violation" in argv


def main() -> None:
    inject = _inject_flag(sys.argv[1:])
    print(f"\n[run_all] inject_violation={inject}\n")

    a = procurement_run.run(inject_violation=inject)
    print()
    b = inflight_run.run(inject_violation=inject)
    print()
    c = cohort_run.run(inject_violation=inject)
    print()

    print("=" * 64)
    print("Hiring scenarios -- combined summary")
    print("=" * 64)
    print(f"  A. Procurement:        overall_passed={a.get('overall_passed')}  "
          f"({a.get('total_ms', 0):.1f} ms)")
    print(f"  B. In-flight:          overall_passed={b.get('overall_passed')}  "
          f"({b.get('total_ms', 0):.1f} ms)")
    # Scenario C does not return a single overall_passed; report findings.
    print(f"  C. Cohort:             corpus={c.get('corpus_size')}  "
          f"4/5 ratio={c.get('four_fifths_ratio'):.3f}  "
          f"4/5 passed={c.get('four_fifths_passed')}  "
          f"incidents passed={c.get('incident_attestation_passed')}  "
          f"({c.get('total_ms', 0):.1f} ms)")
    print("=" * 64)


if __name__ == "__main__":
    main()
