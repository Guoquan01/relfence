"""Run the audit and publish the report details for the composite action."""

import json
import os
import tempfile
from pathlib import Path
from textwrap import indent

from relfence.cli import main as cli


def run():
    manifest = os.environ["RELFENCE_MANIFEST"]
    if not manifest:
        raise SystemExit("The manifest input is required.")
    report_path = Path(os.environ["RELFENCE_OUT"])
    # A normal CLI return guarantees this invocation wrote the report.
    code = cli(["check", f"--manifest={manifest}", f"--out={report_path}"])
    report_bytes = report_path.read_bytes()

    # The uploader treats its path as a glob, so stage only the report bytes.
    staging = Path(tempfile.mkdtemp(prefix="relfence-report-", dir=os.environ["RUNNER_TEMP"]))
    artifact_path = staging / "relfence-report.json"
    artifact_path.write_bytes(report_bytes)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as outputs:
        print(f"exit-code={code}", file=outputs)
        print(f"artifact-name={staging.name}", file=outputs)
        print(f"artifact-path={artifact_path.as_posix()}", file=outputs)

    report = json.loads(report_bytes)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        print(f"### RelFence · {report['verdict']} (exit {code})\n", file=summary)
        if "reason" in report:
            print(indent(report["reason"], "    "), file=summary)
        else:
            print(f"{report['input_count']} inputs checked, {report['violation_count']} violations.", file=summary)
        if report["witnesses"]:
            print("\nReplay the DuckDB SQL from the directory containing the manifest.", file=summary)
        for witness in report["witnesses"]:
            print(f"\n#### {witness['violation_code']} witness\n", file=summary)
            print(indent(witness["repro_duckdb_sql"], "    "), file=summary)
    return code


if __name__ == "__main__":
    raise SystemExit(run())
