"""Exercise the installed CLI entry point and replay each proof on the raw CSVs."""

import hashlib
import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

import duckdb
import pytest

SEED = Path(__file__).resolve().parents[1] / "examples" / "seed"
CLI = distribution("relfence").entry_points.select(group="console_scripts")["relfence"].load()


def run_check(monkeypatch, manifest, report):
    monkeypatch.setattr(sys, "argv", ["relfence", "check", "--manifest", str(manifest), "--out", str(report)])
    try:
        result = CLI()
    except SystemExit as exc:
        result = exc.code
    return result or 0, json.loads(report.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "case,exit_code,verdict,code,delta",
    [
        ("clean", 0, "PASS", None, None),
        ("l1", 1, "FAIL_LEAKAGE", "L1", 2 * 86400),
        ("l2", 1, "FAIL_LEAKAGE", "L2", 86400),
        ("l3", 1, "FAIL_LEAKAGE", "L3", 7 * 86400),
        ("missing", 2, "INSUFFICIENT_EVIDENCE", None, None),
    ],
)
def test_pinned_seed(case, exit_code, verdict, code, delta, tmp_path, monkeypatch):
    status, report = run_check(monkeypatch, SEED / f"{case}.manifest.json", tmp_path / "report.json")
    assert (status, report["verdict"]) == (exit_code, verdict)
    if code is None:
        return
    witness, = report["witnesses"]
    assert witness["violation_code"] == code
    assert witness["offending_row"]["delta_seconds"] == delta
    monkeypatch.chdir(SEED)
    with duckdb.connect(":memory:") as replay:
        row = replay.execute(witness["repro_duckdb_sql"])
        columns = [description[0] for description in row.description]
        results = row.fetchall()
    assert len(results) == 1
    assert results[0][columns.index("delta_seconds")] == delta
    if code == "L2":
        assert {"u001", "p001", "v002"} <= set(results[0])


@pytest.fixture
def copied_seed(tmp_path):
    return Path(shutil.copytree(SEED, tmp_path / "seed"))


def test_checksum_tamper(copied_seed, tmp_path, monkeypatch):
    with (copied_seed / "tables" / "votes.csv").open("ab") as output:
        output.write(b"v999,p001,2024-05-31T11:00:00Z\n")
    status, report = run_check(monkeypatch, copied_seed / "clean.manifest.json", tmp_path / "report.json")
    assert (status, report["verdict"]) == (2, "INSUFFICIENT_EVIDENCE")


@pytest.mark.parametrize("strict,expected", [(True, (1, "FAIL_LEAKAGE")), (False, (0, "PASS"))])
def test_availability_at_cutoff(strict, expected, copied_seed, tmp_path, monkeypatch):
    manifest_path = copied_seed / "l1.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    inputs = manifest["model_inputs"]
    item_file = copied_seed / inputs["uri"]
    item = json.loads(item_file.read_text())
    item["cutoff"] = "2024-06-03T00:00:00Z"
    write_json(item_file, item)
    inputs["sha256"] = hashlib.sha256(item_file.read_bytes()).hexdigest()
    manifest["policy"]["strict_inequality"] = strict
    write_json(manifest_path, manifest)
    status, report = run_check(monkeypatch, manifest_path, tmp_path / "report.json")
    assert (status, report["verdict"]) == expected
    if strict:
        assert report["witnesses"][0]["offending_row"]["delta_seconds"] == 0


def test_disconnected_lineage(copied_seed, tmp_path, monkeypatch):
    manifest_path = copied_seed / "clean.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    inputs = manifest["model_inputs"]
    lineage_file = copied_seed / inputs["lineage_map_uri"]
    record = json.loads(lineage_file.read_text())
    record["join_path"][1]["pk"] = "p003"
    write_json(lineage_file, record)
    inputs["lineage_map_sha256"] = hashlib.sha256(lineage_file.read_bytes()).hexdigest()
    write_json(manifest_path, manifest)
    status, report = run_check(monkeypatch, manifest_path, tmp_path / "report.json")
    assert (status, report["verdict"]) == (2, "INSUFFICIENT_EVIDENCE")


def test_unversioned_mutable_table(copied_seed, tmp_path, monkeypatch):
    manifest_path = copied_seed / "clean.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(table for table in manifest["tables"] if table["name"] == "votes")["is_mutable"] = True
    write_json(manifest_path, manifest)
    status, report = run_check(monkeypatch, manifest_path, tmp_path / "report.json")
    assert (status, report["verdict"]) == (2, "INSUFFICIENT_EVIDENCE")
