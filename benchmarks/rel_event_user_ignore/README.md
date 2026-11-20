# RelBench user-ignore split replay

The public dataset revision is `21d7284df2a9621c71f21bc4a1a30b7e8effb8dd`. The [manifest](./manifest.json) locks both published splits, both upstream manifests and all four source tables by SHA-256. [Pinned dataset files](https://huggingface.co/datasets/stanford-star/relbench-v1/tree/21d7284df2a9621c71f21bc4a1a30b7e8effb8dd/rel-event)

RelBench's pre-fix task generator padded any singleton timestamp batch with an extra anchor seven days earlier. The validation cutoff was November 21, 2012. The extra November 14 anchor was already the last training anchor. PR #357 removed the padding while retaining the seven-day outcome window. The replay also applies the upstream loader's database and dangling-user filters. [Upstream fix](https://github.com/stanford-star/relbench/pull/357) · [Pre-fix source](https://github.com/stanford-star/relbench/blob/04d7af7f0571ace8943cf0469a852c4733d0af53/relbench/tasks/event.py)

The published training split has 19,239 rows across 22 anchors. The pre-fix logic produces 4,185 validation rows, including 2,172 rows with the same user, timestamp, and target as training. The fixed logic reproduces all 2,013 published validation rows exactly. For user `2991`, the old validation output included their November 14 training target of 1, from three invited events. The correct November 21 validation target is 0, from one invited event. `event_attendees` drives the label calculation. The witness also joins the real `events` and `event_interest` rows by event ID to show the supporting slice. The audit measures split overlap at label generation. Model-input leakage and ROC-AUC changes require their own evaluation.

From the repository root, download the pinned files (about 117 MB), verify their checksums, regenerate the witness, and compare it with the checked-in result.

```sh
python -m pip install -e . duckdb==1.5.6
python benchmarks/rel_event_user_ignore/run_trace.py --source-dir ./rel-event-data --out ./rel-event-witness.json
cmp ./rel-event-witness.json benchmarks/rel_event_user_ignore/witness.json
```

To replay just the example independently, run this from the same repository root after the download. The query uses paths relative to `rel-event-data`.

```sh
python - <<'PY'
import json
from pathlib import Path
import duckdb

witness = json.loads(Path("benchmarks/rel_event_user_ignore/witness.json").read_bytes())
with duckdb.connect() as db:
    db.execute("SET file_search_path = 'rel-event-data'")
    result = db.execute(witness["repro_duckdb_sql"])
    row, = result.fetchall()
    print(dict(zip([column[0] for column in result.description], row)))
PY
```
