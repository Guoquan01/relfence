# RelFence

[![DuckDB oracle](https://github.com/Guoquan01/relfence/actions/workflows/core.yml/badge.svg?branch=main)](https://github.com/Guoquan01/relfence/actions/workflows/core.yml) [![Cross-engine Cypher audit](https://github.com/Guoquan01/relfence/actions/workflows/cypher.yml/badge.svg?branch=main)](https://github.com/Guoquan01/relfence/actions/workflows/cypher.yml)

RelFence checks whether the records used to make a prediction were available at the time. On a leak it returns the offending row, the foreign-key path that brought it into the input, and SQL that reproduces the result from the raw tables.

**One vote demonstrates the bug.** On June 1, user `u001` already owned post `p001`. Vote `v002` arrived on June 2. A graph query that filters the post date but skips the vote date lets that vote into the June 1 prediction. DuckDB joins the original rows and reports a difference of exactly **86,400 seconds**. [Read the witness](./examples/seed/l2.witness.json) · [Read the Cypher query](./examples/cypher/query.cypher)

## Reproduce it

The [demo pull request](https://github.com/Guoquan01/relfence/pull/1/files) removes the vote cutoff from the query. The CLI verdict, exact SQL and witness JSON are visible without signing in. [Read the bot's evidence](https://github.com/Guoquan01/relfence/pull/1#issuecomment-7030535539)

Requires Python 3.11+ and a checkout of this repository. Run from its root.

```sh
python -m pip install .
cd examples/seed
relfence check --manifest clean.manifest.json --out report.json  # exit 0
relfence check --manifest l2.manifest.json --out report.json     # exit 1
python -c "import duckdb,json; q=json.load(open('report.json'))['witnesses'][0]['repro_duckdb_sql']; duckdb.sql(q).show()"
relfence check --manifest missing.manifest.json --out report.json  # exit 2
```

The three exit codes distinguish a clean extraction, a proven leak, and a case where the evidence is incomplete. For example, removing the lineage entry for the late vote yields exit 2 even though the raw vote is still present.

| Pinned input | Expected result |
| --- | --- |
| [Clean two-hop path](./examples/seed/clean.manifest.json) | Pass |
| [Future post](./examples/seed/l1.manifest.json) | L1 · direct future record |
| [Future vote](./examples/seed/l2.manifest.json) | L2 · future record on a two-hop path |
| [Immature historical label](./examples/seed/l3.manifest.json) | L3 · label window still open |
| [Missing vote lineage](./examples/seed/missing.manifest.json) | Insufficient evidence |

## Use the GitHub Action

Add these steps to a job on a GitHub-hosted Ubuntu runner. Set `manifest` to the pinned manifest in your checkout.

```yaml
steps:
  - uses: actions/checkout@v6
  - uses: Guoquan01/relfence@main
    with:
      manifest: examples/seed/clean.manifest.json
```

The action uploads the report and includes witness SQL in the job summary. Its `exit-code` output distinguishes a pass (0), proven leakage (1), and insufficient evidence (2). [Action definition](./action.yml) · [CI example](./.github/workflows/core.yml)

## What the alpha covers

The seed contains 200 synthetic rows in four inspectable CSVs. Its DuckDB checker verifies file digests, joins row keys against declared foreign keys and audits UTC availability and label maturity. A separate [stock Memgraph demo](./examples/cypher/README.md) runs the checked-in query and two reference controls against the same graph. CI audits their actual outputs and replays the leaky control's SQL in a fresh DuckDB connection. Core tests run without Docker.

The oracle reads pinned CSV and Parquet tables directly in DuckDB and audits inputs sharing a foreign-key path in one batch. On versioned tables it checks the row version the extractor actually used. Reading a version that starts at or after the prediction cutoff yields an L4 witness. Missing or ambiguous version lineage yields exit 2. A regression test exercises 10,000 two-hop inputs and replays the resulting SQL. [Input contract](./SPEC.md) · [Batch and version tests](./tests/test_parquet_batch.py)

The public audit queue lists benchmark tasks and upstream implementations for future runs. The scoreboard distinguishes a replayed synthetic witness, a historical result reported by its authors, and targets awaiting an audit. [Scoreboard](./SCOREBOARD.md) · [Machine-readable queue](./targets/audit_queue.yaml) · [Audit workstreams](./WORKSTREAMS.md)

The RelArena-α authors report that affected methods scored two to six ROC-AUC points lower after a label fix on `rel-event/user-ignore`. Their result comes from reevaluating the benchmark. The RelFence demo above uses synthetic votes. [RelArena-α §1.2](https://arxiv.org/html/2608.16319v3#S1.SS2) · [Upstream RelBench fix](https://github.com/stanford-star/relbench/pull/357)

Follow the [million-edge benchmark](https://github.com/Guoquan01/relfence/issues/2), [pipeline instrumentation](https://github.com/Guoquan01/relfence/issues/3), [blind and paired evaluations](https://github.com/Guoquan01/relfence/issues/4), and [clean-checkout verification](https://github.com/Guoquan01/relfence/issues/5).
