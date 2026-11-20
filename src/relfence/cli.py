import argparse
import json
from pathlib import Path

import duckdb

from .oracle import check


def main(argv=None):
    parser = argparse.ArgumentParser(prog="relfence")
    subcommands = parser.add_subparsers(dest="command", required=True)
    audit = subcommands.add_parser("check", help="audit a pinned relational input manifest")
    audit.add_argument("--manifest", type=Path, required=True)
    audit.add_argument("--out", type=Path, default=Path("witnesses.json"))
    args = parser.parse_args(argv)
    try:
        report = check(args.manifest)
    except (ValueError, KeyError, OSError, duckdb.DataError, duckdb.IOException,
            duckdb.BinderException, duckdb.InvalidInputException) as error:
        report = {"verdict": "INSUFFICIENT_EVIDENCE", "reason": str(error), "witnesses": []}
    args.out.write_bytes((json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    verdict = report["verdict"]
    if verdict == "FAIL_LEAKAGE":
        codes = ", ".join(witness["violation_code"] for witness in report["witnesses"])
        print(f"[{verdict}] {report['violation_count']} violation(s) ({codes}) | witness -> {args.out}")
        return 1
    if verdict == "INSUFFICIENT_EVIDENCE":
        print(f"[{verdict}] {report['reason']}")
        return 2
    print(f"[{verdict}] {report['input_count']} input(s) checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
