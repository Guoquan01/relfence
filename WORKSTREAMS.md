# Parallel agent assignments

Give each agent its block below in a separate checkout of the same commit. The three agents share the [input and witness contract](./SPEC.md). The public seed fixtures are practice cases. Reserve new cases for the blind evaluation.

### Agent A · Reference oracle

> The alpha loads each CSV once, then runs a DuckDB join per input item. Batch those joins over Parquet by foreign-key path and add as-of reads for versioned rows. Match the seed verdicts and replayable witnesses on the same inputs. Measure elapsed time and peak memory on a pinned input with at least one million lineage edges. Include a clean historical version, a future version, and missing or overlapping intervals. [Oracle acceptance criteria](https://github.com/Guoquan01/relfence/issues/2)

### Agent B · Benchmark adapters

> Start with one prediction on `rel-event/user-ignore` (classification). Export its graph neighborhood and historical context bundle with the row keys, foreign-key hops and timestamps actually read. Pin the dataset version, split and cutoff, and give a command to reproduce each manifest. Once that pair reproduces, target `rel-stack/post-votes` (regression) and `rel-hm/user-item-purchase` (recommendation ranking). Record any input you could not trace. [Task scope and dataset sizes](https://github.com/Guoquan01/relfence/issues/3)

### Agent C · Independent evaluation

> Use the public contract to build clean, leaky and incomplete cases. Keep the new cases and expected verdicts in a separate checkout until the oracle and adapter commits are fixed. Record the case-set digest before integration. Publish expected and observed verdicts, including misses and false alarms. On each evaluated task, keep the model, split, seed and training settings fixed. Publish both scores, their signed difference and the run IDs. Report any rank change among models evaluated in both conditions. [Paired results table](https://github.com/Guoquan01/relfence/issues/4)

### Integration handoff

The reviewer pins the three revisions, reruns the CLI from a fresh checkout and executes the emitted SQL in a new DuckDB connection. Publish commands, manifests, case counts and observed results together. Track multi-task coverage and packaging separately so reviewers can reproduce the first result while later work is in progress.
