"""Compare extracted model inputs with declared foreign keys and pinned raw rows."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .schema import Item, Lineage, Manifest, Table


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def csv_scan(uri):
    return f"read_csv({literal(uri)}, header=true, all_varchar=true, delim=',')"


def pinned_file(directory, uri, digest):
    path = Path(uri)
    if path.anchor or "://" in uri or any(char in uri for char in "*?[]"):
        raise ValueError(f"Expected one manifest-relative file: {uri}")
    path = directory / path
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != digest.lower():
            raise ValueError(f"Checksum mismatch: {uri}")
    return path


def read_records(path, model):
    records = {}
    for line in path.read_bytes().splitlines():
        row = model.model_validate_json(line)
        key = row.prediction_id, row.item_id
        if key in records:
            raise ValueError(f"Duplicate {model.__name__} for {key}")
        records[key] = row
    return records


def utc_text(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def join_columns(left: Table, right: Table):
    # A lineage hop may follow a declared FK in either direction.
    candidates = [
        (fk.column, right.primary_key)
        for fk in left.foreign_keys
        if (fk.references_table, fk.references_column) == (right.name, right.primary_key)
    ] + [
        (left.primary_key, fk.column)
        for fk in right.foreign_keys
        if (fk.references_table, fk.references_column) == (left.name, left.primary_key)
    ]
    if len(candidates) != 1:
        raise ValueError(f"Expected a unique foreign key between {left.name} and {right.name}")
    return candidates[0]


def audit_item(db, manifest, tables, names, item, path, cutoff, manifest_digest):
    task = manifest.task
    if (path[0].table, path[0].pk) != (task.entity_table, item.entity_id):
        raise ValueError(f"Lineage root does not match entity for {item.item_id}")
    if path[-1].pk != item.item_id:
        raise ValueError(f"Lineage terminal key does not match input item {item.item_id}")
    chain = [tables[hop.table] for hop in path]
    if any(table.is_mutable for table in chain):
        raise ValueError("This seed requires immutable source rows on the audited path")

    # Construct the same join twice: materialized tables for checking, raw CSVs for replay.
    sources, replay_sources, where, keys, stamps, hops = [], [], [], [], [], []
    for i, (hop, table) in enumerate(zip(path, chain)):
        pk = f"r{i}.{ident(table.primary_key)}"
        keys.append(f"{pk} AS hop_{i}_pk")
        stamps.append(f"CAST(r{i}.{ident(table.available_at_col)} AS TIMESTAMPTZ)")
        where.append(f"{pk} = {literal(hop.pk)}")
        via = None
        on = ""
        if i:
            left, right = join_columns(chain[i - 1], table)
            on = f" ON r{i-1}.{ident(left)} = r{i}.{ident(right)}"
            via = f"{chain[i-1].name}.{left} = {table.name}.{right}"
        op = "JOIN" if i else "FROM"
        sources.append(f"{op} {names[table.name]} AS r{i}{on}")
        replay_sources.append(f"{op} {csv_scan(table.uri)} AS r{i}{on}")
        hops.append({"hop": i, "table": table.name, "pk": hop.pk, "via_fk": via})

    context = manifest.model_inputs.format == "in_context_examples"
    label_end = f"CAST(r{len(path)-1}.{ident(task.label_window_end_col)} AS TIMESTAMPTZ)"
    fields = [*stamps, label_end] if context else stamps
    filters = " AND ".join(where)
    sql = f"SELECT {', '.join(fields)} {' '.join(sources)} WHERE {filters}"
    rows = db.execute(sql).fetchall()
    if len(rows) != 1 or any(not isinstance(value, datetime) or value.tzinfo is None for value in rows[0]):
        raise ValueError(f"Missing or ambiguous raw row, foreign-key join or timestamp for {item.item_id}")

    times = rows[0]
    strict = manifest.policy.strict_inequality
    candidates = []
    for i, stamp in enumerate(times[:len(path)]):
        if stamp > cutoff or (strict and stamp == cutoff):
            code = "L2" if i > 0 and len(path) > 2 else "L1"
            candidates.append((i, code, stamps[i], ">=" if strict else ">", stamp))
    if context and times[-1] > cutoff:
        candidates.append((len(path) - 1, "L3", label_end, ">", times[-1]))

    for i, code, checked_expr, relation, stamp in candidates:
        cutoff_text = utc_text(cutoff)
        bound = f"TIMESTAMPTZ {literal(cutoff_text)}"
        projections = [*keys, f"{stamps[i]} AS available_at", f"{bound} AS prediction_cutoff"]
        if code == "L3":
            projections.append(f"{label_end} AS label_window_end")
        projections.append(f"epoch({checked_expr} - {bound}) AS delta_seconds")
        replay = (
            "SET TimeZone = 'UTC';\nSELECT " + ", ".join(projections) + "\n"
            + "\n".join(replay_sources) + "\n"
            + f"WHERE {filters} AND {checked_expr} {relation} {bound};"
        )
        row = {
            "table": chain[i].name,
            "primary_key": {chain[i].primary_key: path[i].pk},
            "available_at": utc_text(times[i]),
            "delta_seconds": (stamp - cutoff).total_seconds(),
        }
        if code == "L3":
            row["label_window_end"] = utc_text(stamp)
        yield {
            "schema_version": "0.1.0", "verdict": "FAIL_LEAKAGE", "violation_code": code,
            "task_id": task.task_id, "prediction_id": item.prediction_id, "item_id": item.item_id,
            "prediction_cutoff": cutoff_text, "offending_row": row, "join_path": hops,
            "repro_duckdb_sql": replay, "manifest_sha256": manifest_digest,
        }


def check(manifest_path):
    manifest_bytes = manifest_path.read_bytes()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = Manifest.model_validate_json(manifest_bytes)
    directory = manifest_path.parent
    config = manifest.model_inputs
    items = read_records(pinned_file(directory, config.uri, config.sha256), Item)
    lineage = read_records(
        pinned_file(directory, config.lineage_map_uri, config.lineage_map_sha256), Lineage
    )
    if not items or items.keys() != lineage.keys():
        raise ValueError("Expected one matching lineage record for every extracted input")
    tables = {table.name: table for table in manifest.tables}
    if len(tables) != len(manifest.tables):
        raise ValueError("Duplicate source table name")
    task = manifest.task
    if tables[task.entity_table].primary_key != task.entity_pk:
        raise ValueError("Entity key differs from declared primary key")

    names = {}
    witnesses = []
    predictions = {}
    with duckdb.connect() as db:
        db.execute("SET TimeZone = 'UTC'")
        for n, table in enumerate(manifest.tables):
            path = pinned_file(directory, table.uri, table.sha256)
            if path.suffix.lower() != ".csv":
                raise ValueError(f"Unsupported raw table format: {table.uri}")
            name = names[table.name] = f"raw_{n}"
            db.execute(f"CREATE TABLE {name} AS SELECT * FROM {csv_scan(str(path.resolve()))}")
            pk = ident(table.primary_key)
            if db.execute(f"SELECT count(*) != count(DISTINCT {pk}) FROM {name}").fetchone()[0]:
                raise ValueError(f"Missing or duplicate primary key in {table.name}")

        for key, item in sorted(items.items()):
            raw_cutoff = item.model_extra.get(task.prediction_cutoff_col)
            if not isinstance(raw_cutoff, str):
                raise ValueError(f"Missing prediction cutoff for {key}")
            cutoff = datetime.fromisoformat(raw_cutoff)
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=UTC)
            identity = (item.entity_id, cutoff)
            if predictions.setdefault(item.prediction_id, identity) != identity:
                raise ValueError(f"Conflicting entity or cutoff for {item.prediction_id}")
            witnesses.extend(audit_item(
                db, manifest, tables, names, item, lineage[key].join_path, cutoff, digest
            ))
    witnesses.sort(key=lambda w: (
        len(w["join_path"]), -w["offending_row"]["delta_seconds"],
        w["prediction_id"], w["item_id"], w["violation_code"],
    ))
    smallest = {}
    for witness in witnesses:
        smallest.setdefault(witness["violation_code"], witness)
    return {
        "verdict": "FAIL_LEAKAGE" if witnesses else "PASS",
        "manifest_sha256": digest,
        "strict_inequality": manifest.policy.strict_inequality,
        "input_count": len(items),
        "violation_count": len(witnesses),
        "witnesses": list(smallest.values()),
    }
