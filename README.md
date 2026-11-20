# RelFence

[![DuckDB oracle](https://github.com/Guoquan01/relfence/actions/workflows/core.yml/badge.svg?branch=main)](https://github.com/Guoquan01/relfence/actions/workflows/core.yml) [![Cross-engine Cypher audit](https://github.com/Guoquan01/relfence/actions/workflows/cypher.yml/badge.svg?branch=main)](https://github.com/Guoquan01/relfence/actions/workflows/cypher.yml)

RelFence checks whether the records used to make a prediction were available at the time. On a leak it returns the offending row, the foreign-key path that brought it into the input, and SQL that reproduces the result from the raw tables.

**One vote demonstrates the bug.** On June 1, user `u001` already owned post `p001`. Vote `v002` arrived on June 2. A graph query that filters the post date but skips the vote date lets that vote into the June 1 prediction. DuckDB joins the original rows and reports a difference of exactly **86,400 seconds**. [Read the witness](./examples/seed/l2.witness.json) · [Compare the Cypher predicates](./examples/cypher/extract.py)

## Reproduce it

You can also inspect the [deliberately failing pull request](https://github.com/Guoquan01/relfence/pull/1). It changes one line to select the extraction with the missing second-hop cutoff. The check summary includes the gate verdict and the replayable query.

Requires Python 3.11+ and a checkout of this repository. Run from its root.

```sh
python -m pip install .
cd examples/seed
relfence check --manifest clean.manifest.json --out report.json  # exit 0
relfence check --manifest l2.manifest.json --out report.json     # exit 1
python -c "import duckdb,json; q=json.load(open('report.json'))['witnesses'][0]['repro_duckdb_sql']; print(duckdb.sql(q))"
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

## What the alpha covers

The seed contains 200 synthetic rows in four inspectable CSVs. Its DuckDB checker verifies file digests, joins row keys against declared foreign keys and audits UTC availability and label maturity. A separate [stock Memgraph demo](./examples/cypher/README.md) extracts the graph inputs with Cypher. Its CI job runs both queries, audits their actual outputs and replays the SQL in a fresh DuckDB connection. Core tests run without Docker.

This alpha supports immutable CSV rows, single-column keys, and one recorded lineage path per item. An empty extraction, an unresolved join, or a read from a mutable table yields insufficient evidence. Full RelBench adapters, blind evaluation, and model score comparisons are planned. [Input contract](./SPEC.md) · [Parallel agent assignments](./WORKSTREAMS.md)

For a real benchmark example of why this matters, the RelArena-α authors report that affected methods scored two to six ROC-AUC points lower after a label fix on `rel-event/user-ignore`. Their result comes from reevaluating the benchmark. The RelFence demo above uses synthetic votes. [RelArena-α §1.2](https://arxiv.org/html/2608.16319#S1.SS2) · [Upstream RelBench fix](https://github.com/snap-stanford/relbench/pull/357)

Roadmap: [versioned rows](https://github.com/Guoquan01/relfence/issues/2) · [benchmark adapters](https://github.com/Guoquan01/relfence/issues/3) · [blind and paired evaluation](https://github.com/Guoquan01/relfence/issues/4) · [packaging and reproducibility](https://github.com/Guoquan01/relfence/issues/5).
