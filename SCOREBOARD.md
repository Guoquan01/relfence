# Temporal leakage scoreboard

## Reproduced synthetic control

| Target | Result | Evidence |
| --- | --- | --- |
| `synthetic/user-activity` | `FAIL_LEAKAGE` (`L2`). A June 2 vote entered a June 1 prediction through `users/u001 → posts/p001 → votes/v002`, exactly 86,400 seconds after the cutoff. | [Pinned seed manifest](https://github.com/Guoquan01/relfence/blob/96a780c9e2e0dedfe0ce764e099162c405ff47d9/examples/seed/l2.manifest.json) · [Replayable seed witness](https://github.com/Guoquan01/relfence/blob/96a780c9e2e0dedfe0ce764e099162c405ff47d9/examples/seed/l2.witness.json) · [Public Cypher CI witness on PR #1](https://github.com/Guoquan01/relfence/pull/1#issuecomment-7030535539) |

The checked-in seed uses fixture commit `96a780c9e2e0dedfe0ce764e099162c405ff47d9` and manifest SHA-256 `6a78be89089887b8fa5dfccb72977f2c0579e997c971b883446c1dbf08c30e30`. Replay the witness in a fresh DuckDB connection from `examples/seed/` at that commit. The PR comment reports the same late vote from the live Memgraph extraction and carries its generated manifest digest. [Cross-engine replay instructions](./examples/cypher/README.md)

## Published benchmark reference

Hayler et al. report that affected methods scored **2–6 ROC-AUC points lower** on `rel-event/user-ignore` after RelBench corrected the task labels in 2026. RelFence's evaluation of this task is queued. [RelArena-α §1.2](https://arxiv.org/html/2608.16319v3#S1.SS2) · [Upstream label fix](https://github.com/stanford-star/relbench/pull/357)

## Queued audits

| Scope | Targets | Status |
| --- | ---: | --- |
| Original RelBench v1 | 30 tasks across 7 databases | Queued |
| Selected RelBench v2 additions | 4 tasks across 4 databases | Queued |
| Public code | 1 official RelBench baseline repository and 4 external repositories | Queued |
| arXiv paper and code discovery | 1 weekly scan | Proposed |

The queue records public target IDs and source revisions. VM dispatch, independent replay of third-party findings, and paired model scores are pending. [Machine-readable queue](./targets/audit_queue.yaml) · [Workstreams](./WORKSTREAMS.md)

Every public `FAIL_LEAKAGE` entry requires a full source commit SHA, a manifest SHA-256 with pinned inputs, and a self-contained `repro_duckdb_sql` with the working directory and replay instructions. Replay must succeed from a clean checkout before the team prepares an upstream issue or pull request. Publish paired scores only after both runs use the same data revision, split, seed, model, and training settings.
