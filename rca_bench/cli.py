"""Command-line entry point for the RCA benchmark layer.

This wraps :class:`rca_bench.harness.RCAEvalHarness` so the benchmark can be run
as either ``python -m rca_bench.cli`` or the installed ``rca-bench`` console
script. The legacy ``scripts/run_rca_eval.py`` script delegates here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from argparse import Namespace
from typing import Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full-lifecycle RCA benchmark on a live MicroRemed cluster. "
            "The RCA algorithm receives only a label-free multimodal context; "
            "oracle labels are used only for scoring and verification."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--experiment-path", required=True,
                        help="File with one failure type per line.")
    parser.add_argument("--rca-method", default="planrca",
                        help="RCA adapter name: planrca | metric | random | llmrca.")
    parser.add_argument("--remediate", action="store_true",
                        help="Also run report-driven remediation and score recovery.")
    parser.add_argument("--remediate-method", default="PlanRCAThinkRemed",
                        help=argparse.SUPPRESS)
    parser.add_argument("--expose-artifacts", action="store_true",
                        help="Expose Chaos Mesh CRDs to RCA; useful only for upper-bound/debug runs.")
    parser.add_argument("--no-logs", action="store_true",
                        help="Skip kubectl logs collection for faster runs.")
    parser.add_argument("--jaeger-url", default="http://127.0.0.1:30686",
                        help="Jaeger query API for trace signals; pass '' to disable.")
    parser.add_argument("--model", default="gpt-5.4-mini",
                        help="Model name forwarded to MicroRemed config for optional remediation.")
    parser.add_argument("--microremed-root", default="../MicroRemed",
                        help="Path to the MicroRemed repository.")
    parser.add_argument("--injection-timeout", type=int, default=60)
    parser.add_argument("--wait-interval", type=int, default=10)
    parser.add_argument("--out", default=None, help="Write JSON results here.")
    return parser


def _read_failures(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]


def _write_results(path: str, args: argparse.Namespace, result: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {
        "method": args.rca_method,
        "rca_metrics": result["rca_metrics"].to_dict(),
        "n_incidents": result["n_incidents"],
        "n_injected": result["n_injected"],
        "n_scored": result["n_scored"],
        "remediation": result["remediation"],
        "incidents": [
            {
                "id": outcome.incident_id,
                "failure_type": outcome.failure_type,
                "target": outcome.target_service,
                "injected": outcome.injected,
                "rank_of_truth": (
                    outcome.rca_eval.rank_of_truth() if outcome.rca_eval else None
                ),
                "predicted_top": (
                    outcome.rca_eval.predicted_ranking[:3] if outcome.rca_eval else []
                ),
                "predicted_category": (
                    outcome.rca_eval.predicted_category if outcome.rca_eval else None
                ),
                "remediated": outcome.remediated,
                "note": outcome.note,
            }
            for outcome in result["outcomes"]
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    if args.microremed_root not in sys.path:
        sys.path.insert(0, args.microremed_root)

    from config import set_config

    set_config(Namespace(model=args.model))

    from rca_bench.adapters import get_rca
    from rca_bench.context_provider import LiveClusterContextProvider
    from rca_bench.harness import RCAEvalHarness

    trace_source = None
    if args.jaeger_url:
        from rca_bench.trace_sources import JaegerTraceSource

        trace_source = JaegerTraceSource(args.jaeger_url)

    failures = _read_failures(args.experiment_path)
    provider = LiveClusterContextProvider(
        hide_injection_artifacts=not args.expose_artifacts,
        collect_logs=not args.no_logs,
        trace_source=trace_source,
    )
    harness = RCAEvalHarness(
        get_rca(args.rca_method),
        namespace=args.namespace,
        env=args.env,
        manifest_path=args.manifest_path,
        provider=provider,
        remediate=args.remediate,
        model=args.model,
        injection_timeout=args.injection_timeout,
        wait_interval=args.wait_interval,
    )

    print(
        f"[rca_bench] method={args.rca_method} incidents={len(failures)} "
        f"expose_artifacts={args.expose_artifacts} remediate={args.remediate}"
    )
    result = harness.run(failures)

    print("\n" + result["rca_report"])
    print(
        f"\nincidents={result['n_incidents']} injected={result['n_injected']} "
        f"scored={result['n_scored']}"
    )
    if result["remediation"]:
        remediation = result["remediation"]
        print(
            f"remediation: {remediation['success']}/{remediation['n']} = "
            f"{100 * remediation['rate']:.1f}%"
        )

    if args.out:
        _write_results(args.out, args, result)
        print(f"[rca_bench] wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
