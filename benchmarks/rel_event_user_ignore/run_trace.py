"""Replay RelBench's user-ignore split fix against pinned public Parquet."""

import argparse
import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from urllib.request import urlopen

import duckdb


ROOT = Path(__file__).resolve().parent


def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sources(directory, manifest):
    base = f"{manifest['dataset_url']}/resolve/{manifest['dataset_revision']}/rel-event"
    for file, expected in manifest["sha256"].items():
        path = directory / file
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(path.suffix + ".partial")
            with urlopen(f"{base}/{file}", timeout=60) as response, partial.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if sha256(partial) != expected:
                raise ValueError(f"Invalid downloaded SHA-256 for {file}")
            partial.replace(path)
        elif sha256(path) != expected:
            raise ValueError(f"Invalid cached SHA-256 for {file}")


LABELS = """
SELECT anchor.timestamp, attendee.user_id AS "user",
       count(*) FILTER (WHERE attendee.status = 'invited') AS invitations,
       (count(*) FILTER (WHERE attendee.status = 'invited') > 2)::INTEGER AS target
FROM anchors anchor
JOIN attendees attendee
  ON attendee.start_time > anchor.timestamp
 AND attendee.start_time <= anchor.timestamp + INTERVAL '7 days'
WHERE attendee.user_id IS NOT NULL
GROUP BY anchor.timestamp, attendee.user_id
"""


def replay_sql(user, cutoff):
    return f"""
WITH anchor AS (
    SELECT {user:d}::BIGINT AS "user", TIMESTAMP '{cutoff:%Y-%m-%d %H:%M:%S}' AS cutoff
), evidence AS (
    SELECT a.cutoff, a."user", ea.event, ea.status, ea.start_time,
           e.start_time AS event_start_time,
           (
               SELECT list(struct_pack(user_id := ei."user", interest_timestamp := ei.timestamp, invited := ei.invited)
                           ORDER BY ei.timestamp, ei."user", ei.invited)
               FROM read_parquet('db/event_interest.parquet') ei WHERE ei.event = ea.event
           ) AS interest_rows
    FROM anchor a
    JOIN read_parquet('db/event_attendees.parquet') ea
      ON ea.user_id = a."user" AND ea.start_time > a.cutoff - INTERVAL '7 days'
     AND ea.start_time <= a.cutoff + INTERVAL '7 days'
    JOIN read_parquet('db/events.parquet') e ON e.event_id = ea.event
), windows AS (
    SELECT "user", cutoff,
           count(*) FILTER (WHERE start_time <= cutoff AND status = 'invited') AS padded_invitations,
           count(*) FILTER (WHERE start_time > cutoff AND status = 'invited') AS safe_invitations,
           list(struct_pack(event := event, status := status, start_time := start_time,
                            event_start_time := event_start_time,
                            interest_rows := coalesce(interest_rows, []))
                ORDER BY start_time, event, status) AS source_rows
    FROM evidence GROUP BY "user", cutoff
)
SELECT w.*, old_train.timestamp AS padded_anchor, old_train.target AS train_target,
       (w.padded_invitations > 2)::INTEGER AS old_validation_target,
       published_val.target AS fixed_validation_target
FROM windows w
JOIN read_parquet('tasks/user-ignore/train.parquet') old_train
  ON old_train."user" = w."user" AND old_train.timestamp = w.cutoff - INTERVAL '7 days'
JOIN read_parquet('tasks/user-ignore/val.parquet') published_val
  ON published_val."user" = w."user" AND published_val.timestamp = w.cutoff
WHERE old_train.target = (w.padded_invitations > 2)::INTEGER
  AND published_val.target = (w.safe_invitations > 2)::INTEGER
  AND old_train.target <> published_val.target;
""".strip()


def record(result):
    columns = [field[0] for field in result.description]
    row, = result.fetchall()
    return dict(zip(columns, row))


def trace(directory, manifest, manifest_digest):
    with duckdb.connect() as db:
        db.execute("SET file_search_path = ?", [str(directory)])
        # The upstream task loader truncates the database and filters dangling entity IDs.
        test_timestamp = date.fromisoformat(manifest["test_timestamp"])
        db.execute(f"""
            CREATE VIEW train AS SELECT * FROM read_parquet('tasks/user-ignore/train.parquet');
            CREATE VIEW val AS SELECT * FROM read_parquet('tasks/user-ignore/val.parquet');
            CREATE VIEW attendees AS
            SELECT * FROM read_parquet('db/event_attendees.parquet')
            WHERE start_time <= TIMESTAMP '{test_timestamp}' AND user_id < (
                SELECT count(*) FROM read_parquet('db/users.parquet') WHERE joinedAt <= TIMESTAMP '{test_timestamp}'
            );
        """)
        train = record(db.execute("""SELECT count(*) AS rows, count(DISTINCT timestamp) AS anchors,
                                     min(timestamp) AS first_anchor, max(timestamp) AS last_anchor FROM train"""))
        val = record(db.execute("""SELECT count(*) AS rows, count(DISTINCT timestamp) AS anchors,
                                   min(timestamp) AS anchor FROM val"""))
        if val["anchors"] != 1:
            raise ValueError("Expected a singleton validation timestamp for the upstream padding branch")

        # PR #357 removed the padding branch. Its attendee label SQL stayed the same.
        db.execute("""
            CREATE TEMP VIEW anchors AS
            SELECT DISTINCT timestamp FROM val
            UNION ALL
            SELECT DISTINCT timestamp - INTERVAL '7 days' FROM val;
        """)
        db.execute(f"CREATE TEMP TABLE pre_fix_val AS {LABELS}")
        db.execute("""
            CREATE TEMP VIEW fixed_val AS
            SELECT "user", timestamp, target FROM pre_fix_val WHERE timestamp = (SELECT min(timestamp) FROM val);
            CREATE TEMP VIEW padded_val AS
            SELECT * FROM pre_fix_val WHERE timestamp < (SELECT min(timestamp) FROM val);
        """)
        counts = record(db.execute("""
            WITH fixed_diff AS (
                (SELECT * FROM fixed_val EXCEPT ALL SELECT "user", timestamp, target FROM val)
                UNION ALL
                (SELECT "user", timestamp, target FROM val EXCEPT ALL SELECT * FROM fixed_val)
            )
            SELECT (SELECT count(*) FROM pre_fix_val) AS pre_fix_validation_rows,
                   (SELECT count(*) FROM fixed_val) AS fixed_validation_rows,
                   (SELECT count(*) FROM padded_val) AS padded_validation_rows,
                   (SELECT count(*) FROM padded_val JOIN train USING ("user", timestamp)) AS train_validation_overlap,
                   (SELECT count(*) FROM padded_val JOIN train USING ("user", timestamp, target)) AS same_label_overlap,
                   (SELECT count(*) FROM fixed_diff) AS fixed_vs_published_row_differences
        """))
        if counts["fixed_vs_published_row_differences"] or not counts["same_label_overlap"]:
            raise ValueError(f"Published validation split did not confirm the upstream fix: {counts}")
        user, = db.execute("""
            SELECT padded_val."user"
            FROM padded_val JOIN train USING ("user", timestamp, target) JOIN val USING ("user")
            WHERE padded_val.target = 1 AND val.target = 0
            ORDER BY padded_val.invitations, padded_val."user" LIMIT 1
        """).fetchone()
        sql = replay_sql(user, val["anchor"])

    # Prove the witness requires only the pinned files and a fresh DuckDB connection.
    with duckdb.connect() as db:
        db.execute("SET file_search_path = ?", [str(directory)])
        example = record(db.execute(sql))
    return {
        "task_id": "rel-event/user-ignore",
        "finding": "TRAIN_VALIDATION_OVERLAP_IN_PRE_FIX_LABELS",
        "dataset_revision": manifest["dataset_revision"],
        "upstream_before": manifest["upstream_before"],
        "upstream_fix": manifest["upstream_fix"],
        "manifest_sha256": manifest_digest,
        "train": train,
        "val": val,
        "comparison": counts,
        "example": example,
        "repro_duckdb_sql": sql,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    directory = args.source_dir.resolve()
    sources(directory, manifest)
    witness = trace(directory, manifest, sha256(manifest_path))
    payload = json.dumps(witness, default=json_default, indent=2, sort_keys=True) + "\n"
    args.out.write_bytes(payload.encode("utf-8"))
    print(json.dumps(witness["comparison"], sort_keys=True))


if __name__ == "__main__":
    main()
