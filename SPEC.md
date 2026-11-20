# RelFence v0.1 — Temporal leakage audit contract

**Status:** Proposed specification; no implementation shipped. RelFence is a CI gate for exported graph and in-context inputs. It checks what was knowable at prediction time and returns a replayable witness when it proves a leak. See [RelArena-α §1.2](https://arxiv.org/html/2608.16319#S1.SS2) for a motivating benchmark correction.

## Input: `relfence.manifest.json`

| Section | Required fields |
| --- | --- |
| Identity | `schema_version`, `run_id`, `dataset_revision`, `task_id`, `task_type` (classification, regression, ranking). |
| `tables[]`, `relationships[]` | Table `name`, `uri`, `sha256`, `primary_key`, `row_version_col` for mutable data, and `available_at_col`. Each relationship names both tables, join columns and a stable ID. Availability means when that exact version became usable, not when the event happened. |
| `examples[]`, `expected_inputs[]` | `example_id`, versioned `root_ref`, `prediction_cutoff`. Capture expected `(example_id, input_id)` independently at the model-input boundary for the same run; explicitly mark legitimate empty inventories. |
| `model_inputs` | Export and lineage references with SHA-256 digests. Each consumed input has `example_id`, `input_id`, `role`, and ordered paths of versioned row references (table, primary key, version), each step naming the relationship and direction. |
| Context | Context features carry `context_anchor`. Context labels also carry `label_window_end`, `label_available_at`, and contributor lineage. UTC RFC 3339 timestamps; replay uses the declared cutoff policy. |

## Rules and CI verdict

- **L1/L2, row visibility:** Every consumed row version, including the root and intermediate bridge rows, must be available by its effective cutoff. Use the prediction cutoff for prediction features and the historical anchor for context features. The historical anchor must precede prediction; other cutoff checks are inclusive (`<=`).
- **L3, context label readiness:** The label window must have closed and both the label and its contributors must be available by the prediction cutoff. A closed window alone does not prove readiness.
- **`fail_leakage` (1):** At least one verified violation; emit a witness even if other inputs lack evidence.
- **`insufficient_evidence` (2):** No proven leak, but the independent inventory is absent or has missing, extra, duplicate, or untraced inputs; a required timestamp, version, label bound, digest, or join reference is missing, ambiguous, or unverifiable. A current-state overwrite cannot establish historical values.
- **`pass` (0):** All expected inputs are accounted for and all declared checks pass. CI blocks either nonzero result. Opaque pipelines and correlation-only proxy detection are outside the proof scope.

## Witness output: `witnesses.json` (illustrative record)

```json
{
  "schema_version": "0.1", "verdict": "fail_leakage", "rule": "L2",
  "task_id": "demo", "example_id": "p1", "input_id": "g1",
  "prediction_cutoff": "2026-01-10T10:00:00Z",
  "effective_cutoff": "2026-01-10T10:00:00Z",
  "offending": {"table":"merchant","pk":{"id":"m7"},"version":"v2",
                "field":"available_at","value":"2026-01-10T10:05:00Z"},
  "path": [
    {"from":"user:u1","relationship":"order.user_id","direction":"reverse","to":"order:o4"},
    {"from":"order:o4","relationship":"order.merchant_id","direction":"forward","to":"merchant:m7:v2"}
  ],
  "replay": {"manifest_sha256":"<digest>","fixture_ref":"case-01",
             "sql_ref":"replay/case-01.sql","expected_verdict":"fail_leakage"}
}
```

Emit one sufficient ordered path per finding. The replay pins a source slice and DuckDB query that reproduce the violation, not just the join. Evidence gaps instead name the missing fields or unresolved references. Test clean, leaking, and incomplete exports separately, including cutoff equality, against independently written fixtures.
