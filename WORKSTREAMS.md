# Parallel agent assignments

Give each agent its block below in a separate checkout of the same commit. The three agents share the [input and witness contract](./SPEC.md). The public seed fixtures are practice cases. Reserve new cases for the blind evaluation.

### Agent A · Reference oracle

> Work only on the DuckDB checker and its unit tests. Read the contract and the pinned raw tables. Implement as-of reads for a mutable table with version intervals. Prove a future version was used by returning its key, cutoff and interval in runnable SQL. Cover a clean historical version, an exact-boundary case and missing or overlapping intervals. Return a PR, the commands you ran and one replayable witness. Keep extraction code out of this workstream.

### Agent B · Benchmark adapters

> Take one public RelBench task first. Record the dataset version, task name and split so another person can fetch exactly what you used. Export one graph neighborhood and one historical context bundle with the row keys, foreign-key hops and timestamps actually read by each pipeline. Start with one prediction that a reviewer can trace by hand. Return a PR with the two manifests, their reproduction commands and any input you could not trace. Expand to classification, regression and ranking after the first pair reproduces.

### Agent C · Independent evaluation

> Use the public contract to build clean, leaky and incomplete cases. Keep the new cases and expected verdicts in a separate checkout until the oracle and adapter commits are fixed. Record the case-set digest before integration. At integration, publish the cases and a table of expected versus observed verdicts, including misses and false alarms. For one real task, run the clean and leaky extractions with the same model, split, seed and training settings. Report the task metric, paired difference and run IDs, even if the difference is zero.

### Integration handoff

The reviewer pins the three revisions, reruns the CLI from a fresh checkout and executes the emitted SQL in a new DuckDB connection. Publish commands, manifests, case counts and observed results together. Track multi-task coverage and packaging separately so reviewers can reproduce the first result while later work is in progress.
