"""Generate pinned two-hop Parquet inputs and measure the RelFence CLI."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns

import duckdb


MIB = 1024 ** 2
SEED_INTERVAL = 4096


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path.write_bytes((json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def parquet(db, directory, name, query):
    path = directory / f"{name}.parquet"
    sql_path = "'" + path.as_posix().replace("'", "''") + "'"
    db.execute(f"COPY ({query}) TO {sql_path} (FORMAT PARQUET, COMPRESSION ZSTD)")
    return {"uri": path.name, "sha256": digest(path)}


def generate(directory, paths):
    with duckdb.connect() as db:
        db.execute("SET threads = 1")
        db.execute(f"""
            CREATE TEMP VIEW fixture AS
            SELECT i, i::VARCHAR AS id, (i % 256)::VARCHAR AS user_id,
                   i % {SEED_INTERVAL} = 0 AS late_vote,
                   i % {SEED_INTERVAL} = 1 AS future_post
            FROM range({paths}) AS v(i)
        """)
        data = {
            "users": parquet(db, directory, "users", """
                SELECT i::VARCHAR AS id, TIMESTAMP '2024-04-01' AS available_at
                FROM range(256) AS v(i) ORDER BY i
            """),
            "posts": parquet(db, directory, "posts", """
                SELECT id, user_id, TIMESTAMP '2024-05-01' AS available_at,
                       TIMESTAMP '2024-05-01' AS valid_from,
                       CASE WHEN future_post THEN TIMESTAMP '2024-06-03' END AS valid_to
                FROM fixture
                UNION ALL
                SELECT id, user_id, TIMESTAMP '2024-05-01', TIMESTAMP '2024-06-03', NULL
                FROM fixture WHERE future_post
                ORDER BY id, valid_from
            """),
            "votes": parquet(db, directory, "votes", """
                SELECT id, id AS post_id,
                       CASE WHEN late_vote THEN TIMESTAMP '2024-06-02'
                            ELSE TIMESTAMP '2024-05-01' END AS available_at
                FROM fixture ORDER BY i
            """),
        }
        lineage = parquet(db, directory, "lineage", """
            SELECT id AS prediction_id, id AS item_id, [
                {'table': 'users', 'pk': user_id, 'valid_from': NULL::TIMESTAMP},
                {'table': 'posts', 'pk': id, 'valid_from':
                    CASE WHEN future_post THEN TIMESTAMP '2024-06-03' ELSE TIMESTAMP '2024-05-01' END},
                {'table': 'votes', 'pk': id, 'valid_from': NULL::TIMESTAMP}
            ] AS join_path FROM fixture ORDER BY i
        """)
        inputs = {
            name: parquet(db, directory, name, f"""
                SELECT id AS prediction_id, id AS item_id, user_id AS entity_id,
                       TIMESTAMP '{cutoff}' AS cutoff FROM fixture ORDER BY i
            """)
            for name, cutoff in (("seeded", "2024-06-01"), ("clean", "2024-06-04"))
        }
    tables = []
    for name, fk, parent in (("users", None, None), ("posts", "user_id", "users"), ("votes", "post_id", "posts")):
        table = {**data[name], "name": name, "primary_key": "id", "available_at_col": "available_at",
                 "is_mutable": name == "posts", "foreign_keys": []}
        if fk:
            table["foreign_keys"].append({"column": fk, "references_table": parent, "references_column": "id"})
        if table["is_mutable"]:
            table.update(valid_from_col="valid_from", valid_to_col="valid_to")
        tables.append(table)
    for case, source in inputs.items():
        write_json(directory / f"{case}.manifest.json", {
            "task": {"task_id": "synthetic/million-edges", "task_type": "classification", "entity_table": "users",
                     "entity_pk": "id", "prediction_cutoff_col": "cutoff", "label_window_end_col": "label_end"},
            "tables": tables,
            "model_inputs": {**source, "format": "graph_subgraph", "lineage_map_uri": lineage["uri"],
                             "lineage_map_sha256": lineage["sha256"]},
        })


def peak_rss_bytes():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("page_faults", wintypes.DWORD)] + [
                (field, ctypes.c_size_t) for field in (
                    "peak_working_set", "working_set", "quota_peak_paged", "quota_paged",
                    "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile",
                )
            ]

        counters = ProcessMemoryCounters()
        counters.size = ctypes.sizeof(counters)
        current_process = ctypes.windll.kernel32.GetCurrentProcess
        current_process.restype = wintypes.HANDLE
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        if not get_memory(current_process(), ctypes.byref(counters), counters.size):
            raise ctypes.WinError()
        return counters.peak_working_set

    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss if sys.platform == "darwin" else rss * 1024


def machine_specs():
    cpu = platform.processor()
    if sys.platform == "win32":
        import ctypes
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (field, ctypes.c_uint64) for field in (
                    "total_physical", "available_physical", "total_pagefile", "available_pagefile",
                    "total_virtual", "available_virtual", "available_extended_virtual",
                )
            ]

        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise ctypes.WinError()
        ram = memory.total_physical
    elif sys.platform == "darwin":
        cpu = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        ram = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
    else:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
        ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    return {"os": platform.platform(), "cpu": cpu, "logical_cpus": os.cpu_count(), "ram_bytes": ram,
            "python": platform.python_version(), "duckdb": duckdb.__version__}


def check_case(directory, name, paths, expected_seeded):
    manifest = directory / f"{name}.manifest.json"
    report_file = directory / f"{name}.report.json"
    start = perf_counter_ns()
    process = subprocess.run([
        sys.executable, str(Path(__file__).resolve()), "--measure-check",
        "--manifest", str(manifest), "--out", str(report_file),
    ], check=False, capture_output=True, text=True)
    elapsed = (perf_counter_ns() - start) / 1e9
    expected_codes = {"L2", "L4"} if expected_seeded else set()
    if process.returncode != int(expected_seeded):
        raise RuntimeError(f"{name} CLI exit {process.returncode}\n{process.stdout}{process.stderr}")
    print(process.stdout, end="", file=sys.stderr)
    report = json.loads(report_file.read_bytes())
    witness_codes = {witness["violation_code"] for witness in report["witnesses"]}
    expected_count = sum(len(range(offset, paths, SEED_INTERVAL)) for offset in (0, 1)) if expected_seeded else 0
    if (report["input_count"], report["violation_count"], witness_codes) != (paths, expected_count, expected_codes):
        raise RuntimeError(f"Unexpected audit results for {name}: {report}")
    # Reuse an in-memory connection with the fixture directory as its file search path.
    with duckdb.connect() as db:
        db.execute("SET file_search_path = ?", [str(directory)])
        for witness in report["witnesses"]:
            result = db.execute(witness["repro_duckdb_sql"])
            columns = [column[0] for column in result.description]
            rows = result.fetchall()
            if len(rows) != 1 or rows[0][columns.index("delta_seconds")] != witness["offending_row"]["delta_seconds"]:
                raise RuntimeError(f"Witness replay failed for {witness['violation_code']}")
    manifest_sha256 = digest(manifest)
    if report["manifest_sha256"] != manifest_sha256:
        raise RuntimeError(f"Manifest digest mismatch for {name}")
    peak = int(process.stderr.strip())
    return {"verdict": report["verdict"], "wall_seconds": elapsed, "peak_rss_bytes": peak,
            "peak_rss_mib": peak / MIB, "manifest_sha256": manifest_sha256,
            "violations": expected_count, "witness_codes": sorted(witness_codes)}


def main():
    if sys.argv[1:2] == ["--measure-check"]:
        from relfence.cli import main as relfence

        status = relfence(["check", *sys.argv[2:]])
        print(peak_rss_bytes(), file=sys.stderr)
        return status

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edges", type=int, default=1_000_000)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.edges < 4 or args.edges % 2:
        parser.error("--edges must be even and at least 4")
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = args.edges // 2
    generate(directory, paths)
    results = {"edges_per_case": args.edges, "paths_per_case": paths, "machine": machine_specs(),
               "seeded": check_case(directory, "seeded", paths, True),
               "clean": check_case(directory, "clean", paths, False)}
    write_json(directory / "results.json", results)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
