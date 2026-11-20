# Temporal leakage scoreboard

## Reproduced synthetic control

| Target | Result | Evidence |
| --- | --- | --- |
| `synthetic/user-activity` | `FAIL_LEAKAGE` (`L2`). A June 2 vote entered a June 1 prediction through `users/u001 → posts/p001 → votes/v002`, exactly 86,400 seconds after the cutoff. | [Pinned seed manifest](https://github.com/Guoquan01/relfence/blob/96a780c9e2e0dedfe0ce764e099162c405ff47d9/examples/seed/l2.manifest.json) · [Replayable seed witness](https://github.com/Guoquan01/relfence/blob/96a780c9e2e0dedfe0ce764e099162c405ff47d9/examples/seed/l2.witness.json) · [Public Cypher CI witness on PR #1](https://github.com/Guoquan01/relfence/pull/1#issuecomment-7030535539) |

The checked-in seed uses fixture commit `96a780c9e2e0dedfe0ce764e099162c405ff47d9` and manifest SHA-256 `6a78be89089887b8fa5dfccb72977f2c0579e997c971b883446c1dbf08c30e30`. Replay the witness in a fresh DuckDB connection from `examples/seed/` at that commit. The PR comment reports the same late vote from the live Memgraph extraction and carries its generated manifest digest. [Cross-engine replay instructions](./examples/cypher/README.md)

## Million-edge Parquet benchmark

Each run audited **1,000,000 distinct lineage edges** across 500,000 two-hop paths. DuckDB scanned the pinned model-input and lineage Parquet files in SQL. Both witnesses from the seeded run replayed against the source Parquet.

| Case | Result | Wall time (s) | Peak RSS (MB / MiB) | Manifest SHA-256 |
| --- | --- | ---: | ---: | --- |
| Seeded | `FAIL_LEAKAGE`, 123 L2 and 123 L4 violations | 1.9273901 | 459.218944 / 437.9453125 | `c993bfc38834f7b4b16533ab457619b7c26c0fc43ef5b490803be618d502881e` |
| Clean control | `PASS`, zero violations | 1.8325601 | 458.821632 / 437.56640625 | `c5b195441b2fcf9b1e92cc4813bc8e62e74b9c9afc1f4928f2a7cbd323c45733` |

Measured on 2026-11-20 with Intel Core i5-14400 (16 logical CPUs), 16 GiB RAM (16,817,414,144 bytes available to the OS), Windows 11 build 26300, Python 3.14.10, and DuckDB 1.5.6. Timings cover the CLI subprocess and exclude fixture generation and witness replay. [Benchmark and reproduction command](./benchmarks/million_edges/README.md)

## Reproduced public split

| Target | Result | Evidence |
| --- | --- | --- |
| `rel-event/user-ignore` | Pre-fix validation contained 4,185 rows, including 2,172 exact training rows from the padded 2012-11-14 anchor. The fix reproduced all 2,013 published validation rows with zero differences at the 2012-11-21 cutoff. User `2991` had target 1 at the duplicated anchor and target 0 at the proper validation anchor. | [Pinned manifest](./benchmarks/rel_event_user_ignore/manifest.json) · [Verified SQL witness](./benchmarks/rel_event_user_ignore/witness.json) · [Replay instructions](./benchmarks/rel_event_user_ignore/README.md) |

Dataset revision `21d7284df2a9621c71f21bc4a1a30b7e8effb8dd`, manifest SHA-256 `c8b6a11abda1dbaa512a370f05d1abf6550b869ce4e2032d7ce80dd7c1d29cc0`, and upstream fix `0d47fe0c8a1a51aaf97ab485f4a028e290f97c67`. Hayler et al. separately report **2–6 ROC-AUC points lower** for affected methods after the label correction. [Upstream label fix](https://github.com/stanford-star/relbench/pull/357) · [RelArena-α §1.2](https://arxiv.org/html/2608.16319v3#S1.SS2)

## Queued audits

| Scope | Targets | Status |
| --- | ---: | --- |
| Original RelBench v1 | 30 tasks across 7 databases | `user-ignore` split replayed. Model-input audits queued. |
| Selected RelBench v2 additions | 4 tasks across 4 databases | Queued |
| Public code | 1 official RelBench baseline repository and 4 external repositories | Queued |
| arXiv paper and code discovery | 1 weekly scan | Proposed |

The queue records public target IDs and source revisions. VM dispatch, independent replay of third-party findings, and paired model scores are pending. [Machine-readable queue](./targets/audit_queue.yaml) · [Workstreams](./WORKSTREAMS.md)

Every public `FAIL_LEAKAGE` entry requires a full source commit SHA, a manifest SHA-256 with pinned inputs, and a self-contained `repro_duckdb_sql` with the working directory and replay instructions. Replay must succeed from a clean checkout before the team prepares an upstream issue or pull request. Publish paired scores only after both runs use the same data revision, split, seed, model, and training settings.
