# Audit workstreams

## Repository release gate

RelFence includes a batched DuckDB oracle for CSV and Parquet, validity-interval checks for mutable rows, a reusable GitHub Action, and a Memgraph CI job with runnable SQL witnesses. The public two-hop vote fixture exercises both passing and failing extractions. [Input and witness contract](./SPEC.md) · [Reusable action](./action.yml) · [Cypher controls](./examples/cypher/README.md)

## Planned recurring Devin Bot audits

The proposed workflow assigns these responsibilities to independent checkouts in clean VMs. The [audit queue](./targets/audit_queue.yaml) supplies the public target IDs, starting revisions, and per-repository steps. The [scoreboard](./SCOREBOARD.md) records observed results and pending work.

### 1. Target discovery and lineage tracing

Resolve the repository and data revisions, lock dependencies, and trace the rows and versions the model actually consumes. Export Parquet inputs, the row keys, foreign-key hops, timestamps, and a command to regenerate each manifest. Record any evidence gaps. Begin with `rel-event/user-ignore` (classification), then cover `rel-stack/post-votes` (regression) and `rel-hm/user-item-purchase` (recommendation). Review the proposed weekly arXiv scan for public code that can be queued with a runnable entry point. [Benchmark adapters](https://github.com/Guoquan01/relfence/issues/3)

### 2. Blind evaluation and mutation testing

Build clean, leaky, and incomplete cases in a separate checkout and record the case-set digest before checking the pinned oracle and adapter revisions. Exercise cutoff equality, future backfills, and immature context labels. Include a valid historical row version and cases with missing or overlapping intervals. Publish expected and observed verdicts, misses, and false alarms. Record elapsed time and peak memory on a pinned scale case with at least one million lineage edges. [Oracle acceptance](https://github.com/Guoquan01/relfence/issues/2) · [Blind and paired evaluation](https://github.com/Guoquan01/relfence/issues/4)

### 3. Independent replay and reporting

Rebuild the extraction from a fresh checkout and replay each candidate witness in a new DuckDB connection. For paired evaluations, vary the audited input policy while holding the data revision, split, seed, model, and training settings fixed. Record both run IDs, both scores, and the signed difference in ROC-AUC, MAE, or MAP as appropriate. Report rank changes where the same models were compared in both conditions. Add reproducible results to the scoreboard and draft upstream reports with the pinned source, manifest digest, and replay instructions. [Packaging and reproducibility](https://github.com/Guoquan01/relfence/issues/5)
