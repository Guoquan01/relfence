# Two-hop vote extraction

The extractor runs [query.cypher](./query.cypher) verbatim against Memgraph and writes the result to `active/`. The query starts at user `u001` and walks through post `p001` to its votes. Each returned vote is one model input. At the prediction cutoff of June 1, 2024, only `v001` is available. Removing the vote timestamp predicate admits `v002`, recorded on June 2. The seed stores timestamps as fixed-width UTC strings, so chronological and lexical order agree.

From the repository root, start a fresh local Memgraph container, then run the extractor after Bolt is ready.

```sh
docker run --detach --rm --name relfence-demo -p 127.0.0.1:17687:7687 memgraph/memgraph:3.14.0
python -m pip install '.[cypher]'
python examples/cypher/extract.py --out cypher-output
relfence check --manifest cypher-output/active/manifest.json --out cypher-output/active/report.json
relfence check --manifest cypher-output/clean/manifest.json --out cypher-output/clean/report.json
relfence check --manifest cypher-output/leaky-twohop/manifest.json --out cypher-output/leaky-twohop/report.json
```

The active audit exits 0 on main. Two fixed reference extractions run on the same graph. The clean control exits 0. The leaky control exits 1 and identifies `users/u001 → posts/p001 → votes/v002`. Each output directory includes the extracted vote IDs, their lineage, the manifest and the pinned CSV tables. Replay the witness in a fresh DuckDB connection from the leaky manifest's directory.

```sh
cd cypher-output/leaky-twohop
python -c "import duckdb,json; q=json.load(open('report.json'))['witnesses'][0]['repro_duckdb_sql']; print(duckdb.sql(q))"
docker stop relfence-demo
```

The [CI workflow](../../.github/workflows/cypher.yml) checks the reference votes, replays the control witness and gates the active query using the CLI exit code. On the [demo PR](https://github.com/Guoquan01/relfence/pull/1), the bot publishes the active query's verdict and SQL directly in the conversation.
