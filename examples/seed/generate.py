"""Regenerate the synthetic examples using only the Python standard library."""

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CUTOFF = "2024-06-01T00:00:00Z"


def sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def write_table(name, columns, rows, references=()):
    uri = f"tables/{name}.csv"
    with (ROOT / uri).open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)
    return {
        "name": name,
        "uri": uri,
        "sha256": sha256(uri),
        "primary_key": columns[0],
        "available_at_col": "available_at",
        "foreign_keys": [
            {"column": col, "references_table": target, "references_column": pk}
            for col, target, pk in references
        ],
        "is_mutable": False,
    }


def jsonl(uri, records):
    with (ROOT / uri).open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, separators=(",", ":")) + "\n")
    return sha256(uri)


def bundle(name, tables, paths, *, context=False, omit_last_mapping=False):
    items, mappings = [], []
    for path in paths:
        prediction_id = "pred-" + path[0][1][1:]
        item_id = path[-1][1]
        key = {"prediction_id": prediction_id, "item_id": item_id}
        items.append({**key, "entity_id": path[0][1], "cutoff": CUTOFF})
        mappings.append({**key, "join_path": [{"table": table, "pk": pk} for table, pk in path]})

    inputs = f"inputs/{name}.jsonl"
    lineage = f"inputs/{name}.lineage.jsonl"
    manifest = {
        "tables": tables,
        "task": {
            "task_id": "synthetic/user-activity",
            "task_type": "classification",
            "entity_table": "users",
            "entity_pk": "user_id",
            "prediction_cutoff_col": "cutoff",
            "label_window_end_col": "label_window_end",
        },
        "model_inputs": {
            "format": "in_context_examples" if context else "graph_subgraph",
            "uri": inputs,
            "sha256": jsonl(inputs, items),
            "lineage_map_uri": lineage,
            "lineage_map_sha256": jsonl(lineage, mappings[:-1] if omit_last_mapping else mappings),
        },
        "policy": {"strict_inequality": True, "timezone": "UTC"},
    }
    with (ROOT / f"{name}.manifest.json").open("w", encoding="utf-8", newline="\n") as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")


def main():
    (ROOT / "tables").mkdir(exist_ok=True)
    (ROOT / "inputs").mkdir(exist_ok=True)
    users = [(f"u{i:03}", "2024-01-10T00:00:00Z") for i in range(1, 26)]
    posts = [
        ("p001", "u001", "2024-05-15T00:00:00Z"),
        ("p002", "u002", "2024-06-03T00:00:00Z"),
    ]
    posts.extend(
        (f"p{i:03}", f"u{2 + (i - 3) % 24:03}", "2024-04-15T00:00:00Z")
        for i in range(3, 66)
    )
    votes = [
        ("v001", "p001", "2024-05-31T12:00:00Z"),
        ("v002", "p001", "2024-06-02T00:00:00Z"),
    ]
    votes.extend(
        (f"v{i:03}", f"p{3 + (i - 3) % 63:03}", "2024-05-01T00:00:00Z")
        for i in range(3, 91)
    )
    badges = [
        (f"b{i:03}", f"u{i:03}", "2024-05-01T00:00:00Z", "2024-06-08T00:00:00Z")
        for i in range(1, 21)
    ]
    tables = [
        write_table("users", ("user_id", "available_at"), users),
        write_table("posts", ("post_id", "user_id", "available_at"), posts,
                    [("user_id", "users", "user_id")]),
        write_table("votes", ("vote_id", "post_id", "available_at"), votes,
                    [("post_id", "posts", "post_id")]),
        write_table("badges", ("badge_id", "user_id", "available_at", "label_window_end"), badges,
                    [("user_id", "users", "user_id")]),
    ]
    graph = [("users", "u001"), ("posts", "p001")]
    clean = graph + [("votes", "v001")]
    future = graph + [("votes", "v002")]
    bundle("clean", tables, [clean])
    bundle("l1", tables, [[("users", "u002"), ("posts", "p002")]])
    bundle("l2", tables, [clean, future])
    bundle("l3", tables, [[("users", "u001"), ("badges", "b001")]], context=True)
    bundle("missing", tables, [clean, future], omit_last_mapping=True)


if __name__ == "__main__":
    main()
