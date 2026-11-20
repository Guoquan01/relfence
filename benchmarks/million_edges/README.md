# Million-edge Parquet audit

```sh
python -m pip install -e . duckdb==1.5.6
python benchmarks/million_edges/run_benchmark.py --output-dir ./million-edge-output
```

Each audit joins 500,000 prediction items through `users → posts → votes`. Each post and vote key belongs to one path, for 1,000,000 distinct foreign-key edges per case. DuckDB generates all raw tables, model inputs, and lineage directly as Parquet. Every 4,096 paths the generator selects one late vote (L2) and one future post version (L4). The clean control reads the same paths and versions at a later cutoff.

The script runs the `relfence check` entry point in a fresh Python process for each case. Wall time includes process startup, checksum verification, the audit, and writing the report. Peak RSS is the operating system's process high-water mark (peak working set on Windows). Fixture creation and witness replay occur outside the timed process. The script checks both exact violation counts and replays the two returned SQL witnesses.

The generator writes `seeded.manifest.json`, `clean.manifest.json`, their reports and `results.json` to the output directory. The manifest hashes pin the generated Parquet bytes. The [scoreboard](../../SCOREBOARD.md) records the measured run. CI exercises the same command at 4,096 edges.
