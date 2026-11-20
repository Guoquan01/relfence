import hashlib
import json
import shutil
from pathlib import Path
from time import perf_counter

import duckdb
import pytest

from relfence.cli import main
from relfence.oracle import check


SEED = Path(__file__).resolve().parents[1] / "examples" / "seed"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    return digest(path)


def test_10000_two_hop_parquet_items(tmp_path, monkeypatch):
    """Time the complete audit, including input validation, with fixture creation excluded."""
    root = tmp_path / "seed"
    shutil.copytree(SEED, root)
    monkeypatch.chdir(root)
    path = Path("clean.manifest.json")
    manifest = json.loads(path.read_bytes())
    votes = next(t for t in manifest["tables"] if t["name"] == "votes")
    votes["uri"] = "tables/votes.parquet"
    with duckdb.connect() as db:
        db.execute("""
            COPY (
                SELECT i AS vote_id, 'p001' AS post_id,
                       CASE WHEN i < 2 THEN TIMESTAMP '2024-06-02'
                            ELSE TIMESTAMP '2024-05-30' END AS available_at
                FROM range(10000) AS v(i)
            ) TO 'tables/votes.parquet' (FORMAT PARQUET)
        """)
    votes["sha256"] = digest(Path(votes["uri"]))
    inputs = manifest["model_inputs"]
    records, mappings = [], []
    for i in range(10000):
        key = {"prediction_id": f"pred-{i}", "item_id": str(i)}
        # Both votes 0 and 1 arrive on June 2. Only prediction 0 has the later cutoff.
        cutoff = "2024-06-03T00:00:00Z" if i == 0 else "2024-06-01T00:00:00Z"
        records.append({**key, "entity_id": "u001", "cutoff": cutoff})
        mappings.append({**key, "join_path": [
            {"table": "users", "pk": "u001"}, {"table": "posts", "pk": "p001"},
            {"table": "votes", "pk": str(i)},
        ]})
    inputs["sha256"] = write_jsonl(Path(inputs["uri"]), records)
    inputs["lineage_map_sha256"] = write_jsonl(Path(inputs["lineage_map_uri"]), mappings)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    start = perf_counter()
    first = check(path)
    elapsed = perf_counter() - start
    print(f"10,000 two-hop items audited in {elapsed:.3f}s")
    assert first["verdict"] == "FAIL_LEAKAGE"
    assert (first["input_count"], first["violation_count"]) == (10000, 1)
    assert first == check(path)
    witness, = first["witnesses"]
    assert (witness["violation_code"], witness["prediction_id"]) == ("L2", "pred-1")
    with duckdb.connect() as db:
        result = db.execute(witness["repro_duckdb_sql"])
        row, = result.fetchall()
        columns = [field[0] for field in result.description]
        assert str(row[columns.index("hop_2_pk")]) == "1"
        assert row[columns.index("delta_seconds")] == 86400
    assert elapsed < 1.5


@pytest.mark.parametrize(
    "selected,start,end,cutoff,expected",
    [
        ("2024-05-01", "2024-06-02", "2024-06-02", "2024-06-01", "PASS"),
        ("2024-05-01", "2024-06-02", "2024-06-02", "2024-06-02", "PASS"),
        ("2024-06-02", "2024-06-02", "2024-06-02", "2024-06-01", "L4"),
        ("2024-06-02", "2024-06-02", "2024-06-02", "2024-06-02", "L4"),
        ("2024-06-02", "2024-06-02", "2024-06-02", "2024-06-03", "PASS"),
        (None, "2024-06-02", "2024-06-02", "2024-06-01", "INSUFFICIENT_EVIDENCE"),
        ("2024-05-01", "2024-06-01", "2024-06-03", "2024-06-02", "INSUFFICIENT_EVIDENCE"),
        ("2024-05-01", "2024-06-02", "2024-06-02", "2024-06-03", "INSUFFICIENT_EVIDENCE"),
    ],
)
def test_version_actually_read(selected, start, end, cutoff, expected, tmp_path, monkeypatch):
    root = tmp_path / "seed"
    shutil.copytree(SEED, root)
    monkeypatch.chdir(root)
    path = Path("clean.manifest.json")
    manifest = json.loads(path.read_bytes())
    table = next(t for t in manifest["tables"] if t["name"] == "posts")
    table.update(is_mutable=True, valid_from_col="version_start", valid_to_col="version_end")
    source = Path(table["uri"])
    source.write_text(
        "post_id,user_id,available_at,version_start,version_end\n"
        f"p001,u001,2024-04-01,2024-05-01,{end}\n"
        f"p001,u001,2024-04-01,{start},\n", encoding="utf-8"
    )
    table["sha256"] = digest(source)
    inputs = manifest["model_inputs"]
    item_path = Path(inputs["uri"])
    item = json.loads(item_path.read_bytes())
    item["cutoff"] = cutoff + "T00:00:00Z"
    inputs["sha256"] = write_jsonl(item_path, [item])
    if selected:
        lineage_path = Path(inputs["lineage_map_uri"])
        lineage = json.loads(lineage_path.read_bytes())
        lineage["join_path"][1]["valid_from"] = selected + "T00:00:00Z"
        inputs["lineage_map_sha256"] = write_jsonl(lineage_path, [lineage])
    path.write_text(json.dumps(manifest), encoding="utf-8")

    status = main(["check", "--manifest", str(path), "--out", "report.json"])
    report = json.loads(Path("report.json").read_bytes())
    if expected != "L4":
        assert report["verdict"] == expected
        assert status == (0 if expected == "PASS" else 2)
        return
    assert status == 1
    witness, = report["witnesses"]
    assert (witness["violation_code"], witness["offending_row"]["table"]) == ("L4", "posts")
    with duckdb.connect() as db:
        result = db.execute(witness["repro_duckdb_sql"])
        row, = result.fetchall()
        assert row[[col[0] for col in result.description].index("delta_seconds")] == (
            0 if selected == cutoff else 86400
        )
