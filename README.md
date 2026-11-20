# RelFence

Open-source CI release gate for detecting temporal leakage in relational machine learning pipelines.

**Status:** Phase 0 — v0.1 specification frozen in [SPEC.md](https://www.google.com/search?q=./SPEC.md). Implementation has not started yet.

## Why RelFence

Temporal leakage in multi-table relational benchmarks silently inflates reported metrics. For example, [RelArena-α (§1.2)](https://arxiv.org/html/2608.16319#S1.SS2) reported that affected methods scored 2–6 ROC-AUC points lower on `rel-event/user-ignore` when reevaluated after a temporal-leakage fix.

A single timestamp filter is easy to bypass in relational pipelines: a multi-hop foreign-key join can pull in a record that arrived after prediction time, or an in-context historical training example can include a label whose outcome window had not yet closed at the prediction cutoff.

## Planned v0.1 Behavior

- **Hop-by-hop & maturity verification:** Checks record availability timestamps across multi-hop joins and verifies that historical context labels matured before the prediction cutoff.
- **Minimal runnable witnesses:** On failure, emits the prediction cutoff, offending row, exact join path, and a self-contained DuckDB query reproducing the violation.
- **Fail-closed CI verdicts:** Exits `0` (`PASS`), `1` (`FAIL_LEAKAGE`), or `2` (`INSUFFICIENT_EVIDENCE`) when required timestamps, version history, or row-level lineage are missing—never silently passing an unverified pipeline.

```bash
$ relfence check --manifest relfence.manifest.json --out witnesses.json
[FAIL_LEAKAGE] 1 violation (L2: Multi-Hop Future Bridge) | witness -> witnesses.json
exit status 1
```

## Specification & Roadmap

Full input contract, violation taxonomy (`L1`–`L3`, `E1`), witness JSON schema, and release acceptance criteria are frozen in [SPEC.md](https://www.google.com/search?q=./SPEC.md).

1. **Reference Oracle & CI Gate:** Independent DuckDB checker, witness generator, seeded leak fixtures, and fail-closed `INSUFFICIENT_EVIDENCE` controls.
2. **Dual Pipeline Adapters:** Two independent extraction implementations covering graph subgraphs and in-context examples across classification, regression, and ranking tasks.
3. **Blind Adversarial & Paired Evaluation:** Held-back test fixtures unseen prior to integration, differential cross-checks, and paired clean-vs-leaky evaluations with model and split held fixed.
4. **Release & CI Integration:** PyPI package, GitHub Actions workflow, checksum-verified clean-checkout reproduction of pinned inputs and outputs, and a public cross-workstream audit case study.

## Explicit Proof Limits

- RelFence does not flag contemporaneous proxy columns from statistical correlation alone without explicit derivation rules.
- SHA-256 checksums verify pinned input manifests and emitted witness artifacts from a clean checkout; they do not guarantee bit-exact model training metrics across different hardware or OS environments.

## Reference

- RelArena-α (§1.2): https://arxiv.org/html/2608.16319#S1.SS2
