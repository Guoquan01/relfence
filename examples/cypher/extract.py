"""Run the checked-in Cypher query and its two reference controls on the same graph."""

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

from neo4j import GraphDatabase


SEED = Path(__file__).resolve().parents[1] / "seed"
LOAD = {
    "users": """
        UNWIND $rows AS row
        CREATE (:User {id: row.user_id, available_at: row.available_at})
    """,
    "posts": """
        UNWIND $rows AS row
        MATCH (u:User {id: row.user_id})
        CREATE (:Post {id: row.post_id, available_at: row.available_at})-[:OWNED_BY]->(u)
    """,
    "votes": """
        UNWIND $rows AS row
        MATCH (p:Post {id: row.post_id})
        CREATE (:Vote {id: row.vote_id, available_at: row.available_at})-[:ON_POST]->(p)
    """,
}
TRAVERSAL = """
    MATCH (u:User {id: $entity})<-[:OWNED_BY]-(p:Post)<-[:ON_POST]-(v:Vote)
    WHERE u.available_at < $cutoff AND p.available_at < $cutoff
"""
SECOND_HOP = {"clean": " AND v.available_at < $cutoff", "leaky-twohop": ""}
PROJECTION = " RETURN u.id AS user_id, p.id AS post_id, v.id AS vote_id ORDER BY vote_id"


def load_graph(tx, tables):
    if tx.run("MATCH (n) RETURN 1 LIMIT 1").single():
        raise RuntimeError("Start a fresh Memgraph container for this demo.")
    for name, statement in LOAD.items():
        with (SEED / tables[name]["uri"]).open(encoding="utf-8", newline="") as stream:
            tx.run(statement, rows=list(csv.DictReader(stream))).consume()


def write_jsonl(path, records):
    data = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_extraction(directory, manifest, prediction, rows):
    directory.mkdir(parents=True, exist_ok=True)
    for table in manifest["tables"]:
        name = Path(table["uri"])
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SEED / name, directory / name)

    inputs, lineage = [], []
    for row in rows:
        item = {"prediction_id": prediction["prediction_id"], "item_id": row["vote_id"]}
        inputs.append({**item, "entity_id": row["user_id"], "cutoff": prediction["cutoff"]})
        lineage.append({
            **item,
            "join_path": [
                {"table": "users", "pk": row["user_id"]},
                {"table": "posts", "pk": row["post_id"]},
                {"table": "votes", "pk": row["vote_id"]},
            ],
        })
    model_inputs = {
        "format": "graph_subgraph",
        "uri": "inputs.jsonl",
        "sha256": write_jsonl(directory / "inputs.jsonl", inputs),
        "lineage_map_uri": "lineage.jsonl",
        "lineage_map_sha256": write_jsonl(directory / "lineage.jsonl", lineage),
    }
    (directory / "manifest.json").write_text(
        json.dumps({**manifest, "model_inputs": model_inputs}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{directory.name}: extracted {len(inputs)} vote paths")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=17687)
    args = parser.parse_args()
    manifest = json.loads((SEED / "clean.manifest.json").read_bytes())
    with (SEED / manifest["model_inputs"]["uri"]).open(encoding="utf-8") as stream:
        prediction = json.loads(next(stream))
    tables = {table["name"]: table for table in manifest["tables"]}
    with GraphDatabase.driver(f"bolt://127.0.0.1:{args.port}", auth=("", "")) as driver:
        with driver.session() as session:
            with session.begin_transaction() as tx:
                load_graph(tx, tables)
                tx.commit()
            queries = {
                mode: TRAVERSAL + predicate + PROJECTION for mode, predicate in SECOND_HOP.items()
            }
            queries["active"] = Path(__file__).with_name("query.cypher").read_text(encoding="utf-8")
            for mode, query in queries.items():
                rows = session.run(
                    query,
                    entity=prediction["entity_id"], cutoff=prediction["cutoff"],
                )
                write_extraction(args.out / mode, manifest, prediction, rows)


if __name__ == "__main__":
    main()
