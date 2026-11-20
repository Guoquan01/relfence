"""Compare extracted model inputs with declared foreign keys and pinned raw rows."""

import hashlib
import json
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


def table_scan(uri):
    suffix = Path(uri).suffix.lower()
    if suffix == ".csv":
        return csv_scan(uri)
    if suffix == ".parquet":
        return f"read_parquet({literal(uri)})"
    raise ValueError(f"Unsupported raw table format: {uri}")


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


def parse_timestamp(value):
    stamp = datetime.fromisoformat(value)
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def key_column(table, alias, column):
    reference = f"{alias}.{ident(column)}"
    # Lineage keys are strings. Keep joins exact even when Parquet stores typed keys.
    return f"CAST({reference} AS VARCHAR)" if Path(table.uri).suffix.lower() == ".parquet" else reference


def timestamp_column(alias, column, cast="CAST"):
    return f"{cast}({alias}.{ident(column)} AS TIMESTAMPTZ)"


def cutoff_checks(stamp, compare):
    # UTC TIMESTAMP output avoids requiring a Python timezone library for fetches.
    return [f"CASE WHEN isfinite({stamp}) THEN {stamp}::TIMESTAMP END", f"{stamp} {compare} b.cutoff"]


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


def join_expression(chain, index, columns):
    left, right = columns
    return (f"{key_column(chain[index - 1], f'r{index - 1}', left)} = "
            f"{key_column(chain[index], f'r{index}', right)}")


def load_tables(db, directory, tables, referenced):
    names, histories = {}, {}
    for index, table in enumerate(tables.values()):
        path = pinned_file(directory, table.uri, table.sha256)
        name = names[table.name] = f"raw_{index}"
        db.execute(f"CREATE TEMP TABLE {name} AS SELECT * FROM {table_scan(str(path.resolve()))}")
        pk = key_column(table, "r", table.primary_key)
        if not table.is_mutable:
            if db.execute(f"SELECT count(*) != count(DISTINCT {pk}) FROM {name} r").fetchone()[0]:
                raise ValueError(f"Missing or duplicate primary key in {table.name}")
        elif table.name in referenced:
            if table.valid_from_col is None or table.valid_to_col is None:
                raise ValueError(f"Missing validity columns for mutable table {table.name}")
            history = histories[table.name] = f"history_{index}"
            start = timestamp_column("r", table.valid_from_col, "TRY_CAST")
            end = timestamp_column("r", table.valid_to_col, "TRY_CAST")
            # Adjacent intervals may share a boundary under the (from, to] convention.
            db.execute(f"""
                CREATE TEMP TABLE {history} AS
                WITH intervals AS (
                    SELECT {pk} AS pk, {start} AS valid_from, {end} AS valid_to,
                           r.{ident(table.valid_to_col)} IS NOT NULL AS has_end
                    FROM {name} r
                ), sequenced AS (
                    SELECT *, lead(valid_from) OVER (PARTITION BY pk ORDER BY valid_from) AS next_start
                    FROM intervals
                )
                SELECT pk, count(*) AS version_count, bool_or(
                    valid_from IS NULL OR NOT isfinite(valid_from)
                    OR (has_end AND (valid_to IS NULL OR NOT isfinite(valid_to) OR valid_to <= valid_from))
                    OR (next_start IS NOT NULL AND (valid_to IS NULL OR valid_to > next_start))
                ) AS malformed
                FROM sequenced GROUP BY pk
            """)
    return names, histories


def path_checks(manifest, chain, links, names, histories, keys, versions, context):
    sources = []
    fields = []
    compare = ">=" if manifest.policy.strict_inequality else ">"
    for i, table in enumerate(chain):
        alias = f"r{i}"
        pk = key_column(table, alias, table.primary_key)
        key = keys[i]
        on = [f"{pk} = {key}"]
        if i:
            on.append(join_expression(chain, i, links[i - 1]))
        stamp = timestamp_column(alias, table.available_at_col, "TRY_CAST")
        checks = cutoff_checks(stamp, compare)
        if table.is_mutable:
            history = f"h{i}"
            version = versions[i]
            sources.append(
                f"JOIN {histories[table.name]} {history} ON {history}.pk = {key} "
                f"AND NOT {history}.malformed AND ({version} IS NOT NULL OR {history}.version_count = 1)"
            )
            start = timestamp_column(alias, table.valid_from_col, "TRY_CAST")
            end = timestamp_column(alias, table.valid_to_col, "TRY_CAST")
            on.append(f"({version} IS NULL OR {start} = {version})")
            checks.extend([f"{start}::TIMESTAMP", f"{end}::TIMESTAMP",
                           f"{start} >= b.cutoff", f"{end} < b.cutoff"])
        sources.append(f"JOIN {names[table.name]} {alias} ON {' AND '.join(on)}")
        fields.append(checks)
    if context:
        label = timestamp_column(f"r{len(chain) - 1}", manifest.task.label_window_end_col, "TRY_CAST")
        fields.append(cutoff_checks(label, ">"))
    return fields, " ".join(sources)


def read_path(db, manifest, chain, links, names, histories, entries, context):
    input_columns = {"input_index": "BIGINT", "cutoff": "TIMESTAMPTZ"}
    inputs = [{"input_index": index, "cutoff": utc_text(cutoff)}
              for index, (_, _, cutoff) in enumerate(entries)]
    keys, versions = [], []
    for i, table in enumerate(chain):
        key, version = f"pk_{i}", f"version_{i}"
        keys.append(f"b.{key}")
        versions.append(f"b.{version}")
        input_columns[key] = "VARCHAR"
        if table.is_mutable:
            input_columns[version] = "TIMESTAMPTZ"
        for row, (_, path, _) in zip(inputs, entries):
            row[key] = path[i].pk
            if table.is_mutable and path[i].valid_from is not None:
                row[version] = utc_text(parse_timestamp(path[i].valid_from))
    checks, sources = path_checks(manifest, chain, links, names, histories, keys, versions, context)
    fields = ["b.input_index", *(f"row({', '.join(group)})" for group in checks)]
    # Bind the batch as one value and let DuckDB decode the typed lineage relation.
    sql = ("WITH batch AS (SELECT unnest(from_json(?, ?), recursive := true)) "
           f"SELECT {', '.join(fields)} FROM batch b {sources} ORDER BY b.input_index")
    return db.execute(sql, [json.dumps(inputs), json.dumps([input_columns])]).fetchall()


def make_witness(manifest, manifest_digest, chain, links, item, path, cutoff, results, i, code, delta):
    sources, where, keys, hops = [], [], [], []
    for j, (table, hop) in enumerate(zip(chain, path)):
        alias = f"r{j}"
        pk = key_column(table, alias, table.primary_key)
        keys.append(f"{pk} AS hop_{j}_pk")
        where.append(f"{pk} = {literal(hop.pk)}")
        via = None
        on = ""
        if j:
            left, right = links[j - 1]
            on = f" ON {join_expression(chain, j, (left, right))}"
            via = f"{chain[j-1].name}.{left} = {table.name}.{right}"
        op = "JOIN" if j else "FROM"
        sources.append(f"{op} {table_scan(table.uri)} AS {alias}{on}")
        hop_info = {"hop": j, "table": table.name, "pk": hop.pk, "via_fk": via}
        if table.is_mutable:
            version = utc_text(results[j][2].replace(tzinfo=UTC))
            hop_info["valid_from"] = version
            start = timestamp_column(alias, table.valid_from_col)
            where.append(f"{start} = TIMESTAMPTZ {literal(version)}")
        hops.append(hop_info)

    table = chain[i]
    cutoff_text = utc_text(cutoff)
    bound = f"TIMESTAMPTZ {literal(cutoff_text)}"
    available = timestamp_column(f"r{i}", table.available_at_col)
    projections = [*keys, f"{available} AS available_at", f"{bound} AS prediction_cutoff"]
    row = {
        "table": table.name,
        "primary_key": {table.primary_key: path[i].pk},
        "available_at": utc_text(results[i][0].replace(tzinfo=UTC)),
        "delta_seconds": delta,
    }
    checked = available
    relation = ">=" if manifest.policy.strict_inequality else ">"
    if code == "L3":
        checked = timestamp_column(f"r{i}", manifest.task.label_window_end_col)
        relation = ">"
        projections.append(f"{checked} AS label_window_end")
        row["label_window_end"] = utc_text(results[-1][0].replace(tzinfo=UTC))
    elif code == "L4":
        checked = timestamp_column(f"r{i}", table.valid_from_col)
        relation = ">="
        projections.extend([f"{checked} AS valid_from", f"{timestamp_column(f'r{i}', table.valid_to_col)} AS valid_to"])
        row["valid_from"] = hops[i]["valid_from"]
        end = results[i][3]
        row["valid_to"] = utc_text(end.replace(tzinfo=UTC)) if end is not None else None
    projections.append(f"epoch({checked} - {bound}) AS delta_seconds")
    filters = " AND ".join(where)
    replay = (
        "SET TimeZone = 'UTC';\nSELECT " + ", ".join(projections) + "\n"
        + "\n".join(sources) + "\n"
        + f"WHERE {filters} AND {checked} {relation} {bound};"
    )
    return {
        "schema_version": "0.1.0", "verdict": "FAIL_LEAKAGE", "violation_code": code,
        "task_id": manifest.task.task_id, "prediction_id": item.prediction_id, "item_id": item.item_id,
        "prediction_cutoff": cutoff_text, "offending_row": row, "join_path": hops,
        "repro_duckdb_sql": replay, "manifest_sha256": manifest_digest,
    }


def availability_code(index, chain):
    return "L2" if index > 0 and len(chain) > 2 else "L1"


def remember(smallest, chain, links, item, path, cutoff, results, index, code, stamp):
    delta = (stamp.replace(tzinfo=UTC) - cutoff).total_seconds()
    order = (len(chain), -delta, item.prediction_id, item.item_id, code)
    if code not in smallest or order < smallest[code][0]:
        smallest[code] = order, (chain, links, item, path, cutoff, results, index, code, delta)


def audit_path(db, manifest, chain, names, histories, entries, context, smallest):
    links = [join_columns(left, right) for left, right in zip(chain, chain[1:])]
    rows = read_path(db, manifest, chain, links, names, histories, entries, context)
    if len(rows) != len(entries) or any(row[0] != index for index, row in enumerate(rows)):
        raise ValueError(f"Missing or ambiguous row, foreign-key join or version history for path "
                         f"{' -> '.join(table.name for table in chain)}")

    violation_count = 0
    for (_, *results), (item, path, cutoff) in zip(rows, entries):
        candidates = []
        for i, (stamp, future, *version) in enumerate(results[:len(chain)]):
            if stamp is None:
                raise ValueError(f"Missing or invalid availability timestamp for {item.item_id}")
            if future:
                candidates.append((i, availability_code(i, chain), stamp))
            if version:
                start, _, future_version, expired = version
                if expired:
                    raise ValueError(f"Expired version of {chain[i].name} for {item.item_id}")
                if future_version:
                    candidates.append((i, "L4", start))
        if context:
            label_end, immature = results[-1]
            if label_end is None:
                raise ValueError(f"Missing or invalid label-window end for {item.item_id}")
            if immature:
                candidates.append((len(chain) - 1, "L3", label_end))

        violation_count += len(candidates)
        for i, code, stamp in candidates:
            remember(smallest, chain, links, item, path, cutoff, results, i, code, stamp)
    return violation_count


def input_scan(db, path, name, model, columns):
    if path.suffix.lower() == ".parquet":
        return table_scan(str(path.resolve()))
    records = read_records(path, model)
    values = []
    for row in records.values():
        data = row.model_dump()
        if model is Item:
            cutoff = data.get(columns[-1])
            if not isinstance(cutoff, str):
                raise ValueError(f"Missing prediction cutoff for {row.item_id}")
            data[columns[-1]] = utc_text(parse_timestamp(cutoff))
        else:
            for hop in data["join_path"]:
                if hop["valid_from"] is not None:
                    hop["valid_from"] = utc_text(parse_timestamp(hop["valid_from"]))
        values.append(data)
    # Only the JSONL side of a mixed-format manifest needs Python decoding.
    types = dict.fromkeys(columns, "VARCHAR")
    if model is Lineage:
        types["join_path"] = [{"table": "VARCHAR", "pk": "VARCHAR", "valid_from": "VARCHAR"}]
    db.execute(f"CREATE TEMP TABLE {name} AS SELECT unnest(from_json(?, ?), recursive := true)",
               [json.dumps(values), json.dumps([types])])
    return name


def parquet_batch(db, task, item_file, lineage_file, tables):
    keys = ("prediction_id", "item_id")
    item_columns = (*keys, "entity_id", task.prediction_cutoff_col)
    items = input_scan(db, item_file, "json_items", Item, item_columns)
    lineage = input_scan(db, lineage_file, "json_lineage", Lineage, (*keys, "join_path"))
    item_types = {
        name: kind for name, kind, *_ in db.execute(f"DESCRIBE SELECT * FROM {items}").fetchall()
    }
    mapping = db.sql(f"SELECT prediction_id, item_id, join_path FROM {lineage}")
    prediction_type, item_type, path_type = mapping.types
    if (any(item_types.get(key) != "VARCHAR" for key in (*keys, "entity_id"))
            or str(prediction_type) != "VARCHAR" or str(item_type) != "VARCHAR"
            or path_type.id != "list" or path_type.children[0][1].id != "struct"):
        raise ValueError("Expected Parquet string IDs and a list of lineage structs")
    hop_types = dict(path_type.children[0][1].children)
    if any(str(hop_types.get(key)) != "VARCHAR" for key in ("table", "pk")):
        raise ValueError("Expected string table and pk fields in Parquet lineage")
    cutoff = item_types.get(task.prediction_cutoff_col, "")
    version = str(hop_types.get("valid_from", "VARCHAR"))
    if any(kind not in {"DATE", "VARCHAR"} and not kind.startswith("TIMESTAMP")
           for kind in (cutoff, version)):
        raise ValueError("Expected Parquet cutoff and version fields to contain timestamps")
    pointer = "CAST(hop.valid_from AS VARCHAR)" if "valid_from" in hop_types else "NULL::VARCHAR"
    db.execute(f"""
        CREATE TEMP VIEW batch AS
        SELECT i.prediction_id, i.item_id, i.entity_id,
               TRY_CAST(i.{ident(task.prediction_cutoff_col)} AS TIMESTAMPTZ) AS cutoff,
               list_transform(l.join_path, hop -> struct_pack(
                   "table" := hop."table", pk := hop.pk, valid_from := {pointer}
               )) AS join_path,
               list_transform(l.join_path, hop -> hop."table") AS shape
        FROM {items} i
        FULL JOIN {lineage} l USING (prediction_id, item_id)
    """)
    known = ", ".join(literal(name) for name in tables)
    immutable = ", ".join(literal(name) for name, table in tables.items() if not table.is_mutable)
    unexpected_version = f'OR hop."table" IN ({immutable})' if immutable else ""
    invalid, duplicate, conflicting = db.execute(f"""
        SELECT EXISTS (
            SELECT 1 FROM batch
            WHERE coalesce(prediction_id, '') = '' OR coalesce(item_id, '') = ''
               OR coalesce(entity_id, '') = '' OR cutoff IS NULL OR NOT isfinite(cutoff)
               OR join_path IS NULL OR len(join_path) = 0
               OR join_path[1]."table" IS DISTINCT FROM {literal(task.entity_table)}
               OR join_path[1].pk IS DISTINCT FROM entity_id
               OR join_path[-1].pk IS DISTINCT FROM item_id
               OR len(list_filter(join_path, hop ->
                   hop."table" IS NULL OR hop."table" NOT IN ({known})
                   OR coalesce(hop.pk, '') = ''
                   OR (hop.valid_from IS NOT NULL AND (
                       NOT coalesce(isfinite(TRY_CAST(hop.valid_from AS TIMESTAMPTZ)), false)
                       {unexpected_version}
                   ))
               )) > 0
        ), EXISTS (
            SELECT 1 FROM batch GROUP BY prediction_id, item_id HAVING count(*) > 1
        ), EXISTS (
            SELECT 1 FROM batch GROUP BY prediction_id
            HAVING min(entity_id) != max(entity_id) OR min(cutoff) != max(cutoff)
        )
    """).fetchone()
    if invalid or duplicate:
        raise ValueError("Expected matching Parquet inputs and valid, unique lineage paths")
    if conflicting:
        raise ValueError("Conflicting entity or cutoff for a Parquet prediction")
    groups = db.execute("SELECT shape, count(*) FROM batch GROUP BY shape").fetchall()
    if not groups:
        raise ValueError("Expected at least one matching input and lineage record")
    return groups


def audit_parquet_path(db, manifest, chain, names, histories, shape, expected, smallest):
    links = [join_columns(left, right) for left, right in zip(chain, chain[1:])]
    hops = [f"b.join_path[{i}]" for i in range(1, len(chain) + 1)]
    keys = [f"{hop}.pk" for hop in hops]
    versions = [f"TRY_CAST({hop}.valid_from AS TIMESTAMPTZ)" for hop in hops]
    context = manifest.model_inputs.format == "in_context_examples"
    fields, sources = path_checks(manifest, chain, links, names, histories, keys, versions, context)
    groups = [f"c{i}" for i in range(len(fields))]
    projections = [f"row({', '.join(group)}) AS {alias}" for group, alias in zip(fields, groups)]
    invalid = [f"{alias}[1] IS NULL" for alias in groups]
    candidates = [(i, availability_code(i, chain), f"{alias}[1]", f"{alias}[2]")
                  for i, alias in enumerate(groups[:len(chain)])]
    for i, (table, alias) in enumerate(zip(chain, groups)):
        if table.is_mutable:
            invalid.append(f"{alias}[6]")
            candidates.append((i, "L4", f"{alias}[3]", f"{alias}[5]"))
    if context:
        candidates.append((len(chain) - 1, "L3", f"{groups[-1]}[1]", f"{groups[-1]}[2]"))

    # Aggregate in DuckDB and transfer at most one candidate per hop and violation class.
    payload = f"row(prediction_id, item_id, entity_id, cutoff::TIMESTAMP, join_path, row({', '.join(groups)}))"
    summaries = []
    for _, _, stamp, flag in candidates:
        order = f"row(-epoch({stamp} - cutoff::TIMESTAMP), prediction_id, item_id)"
        summaries.extend([f"count(*) FILTER (WHERE {flag})",
                          f"arg_min({payload}, {order}) FILTER (WHERE {flag})"])
    count, incomplete, *winners = db.execute(f"""
        WITH audited AS (
            SELECT b.prediction_id, b.item_id, b.entity_id, b.cutoff, b.join_path,
                   {', '.join(projections)}
            FROM batch b {sources} WHERE b.shape = ?
        )
        SELECT count(*), bool_or({' OR '.join(invalid)}), {', '.join(summaries)}
        FROM audited
    """, [shape]).fetchone()
    if count != expected or incomplete:
        raise ValueError(f"Missing, ambiguous or invalid rows, timestamps or versions for path {' -> '.join(shape)}")
    for (index, code, _, _), winner in zip(candidates, winners[1::2]):
        if winner is None:
            continue
        prediction_id, item_id, entity_id, cutoff, path, results = winner
        item = Item(prediction_id=prediction_id, item_id=item_id, entity_id=entity_id)
        lineage = Lineage(prediction_id=prediction_id, item_id=item_id, join_path=path)
        stamp = results[-1][0] if code == "L3" else results[index][2 if code == "L4" else 0]
        remember(smallest, chain, links, item, lineage.join_path, cutoff.replace(tzinfo=UTC),
                 results, index, code, stamp)
    return sum(winners[::2])


def check(manifest_path):
    manifest_bytes = manifest_path.read_bytes()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = Manifest.model_validate_json(manifest_bytes)
    directory = manifest_path.parent
    config = manifest.model_inputs
    item_file = pinned_file(directory, config.uri, config.sha256)
    lineage_file = pinned_file(directory, config.lineage_map_uri, config.lineage_map_sha256)
    tables = {table.name: table for table in manifest.tables}
    if len(tables) != len(manifest.tables):
        raise ValueError("Duplicate source table name")
    task = manifest.task
    if tables[task.entity_table].primary_key != task.entity_pk:
        raise ValueError("Entity key differs from declared primary key")

    smallest = {}
    with duckdb.connect() as db:
        db.execute("SET TimeZone = 'UTC'")
        if any(path.suffix.lower() == ".parquet" for path in (item_file, lineage_file)):
            groups = parquet_batch(db, task, item_file, lineage_file, tables)
            names, histories = load_tables(db, directory, tables, {name for shape, _ in groups for name in shape})
            input_count = sum(count for _, count in groups)
            violation_count = sum(
                audit_parquet_path(db, manifest, [tables[name] for name in shape], names, histories,
                                   shape, count, smallest)
                for shape, count in groups
            )
        else:
            input_count, violation_count = audit_jsonl(db, manifest, directory, tables,
                                                       item_file, lineage_file, smallest)
    witnesses = [make_witness(manifest, digest, *args) for _, args in sorted(smallest.values())]
    return {
        "verdict": "FAIL_LEAKAGE" if witnesses else "PASS",
        "manifest_sha256": digest,
        "strict_inequality": manifest.policy.strict_inequality,
        "input_count": input_count,
        "violation_count": violation_count,
        "witnesses": witnesses,
    }


def audit_jsonl(db, manifest, directory, tables, item_file, lineage_file, smallest):
    items = read_records(item_file, Item)
    lineage = read_records(lineage_file, Lineage)
    if not items or items.keys() != lineage.keys():
        raise ValueError("Expected one matching lineage record for every extracted input")
    task = manifest.task
    groups = {}
    predictions = {}
    referenced = set()
    for key, item in sorted(items.items()):
        raw_cutoff = item.model_extra.get(task.prediction_cutoff_col)
        if not isinstance(raw_cutoff, str):
            raise ValueError(f"Missing prediction cutoff for {key}")
        cutoff = parse_timestamp(raw_cutoff)
        identity = (item.entity_id, cutoff)
        if predictions.setdefault(item.prediction_id, identity) != identity:
            raise ValueError(f"Conflicting entity or cutoff for {item.prediction_id}")

        path = lineage[key].join_path
        if (path[0].table, path[0].pk) != (task.entity_table, item.entity_id):
            raise ValueError(f"Lineage root does not match entity for {item.item_id}")
        if path[-1].pk != item.item_id:
            raise ValueError(f"Lineage terminal key does not match input item {item.item_id}")
        for hop in path:
            if hop.valid_from is not None and not tables[hop.table].is_mutable:
                raise ValueError(f"Unexpected version pointer for immutable table {hop.table}")
        shape = tuple(hop.table for hop in path)
        groups.setdefault(shape, []).append((item, path, cutoff))
        referenced.update(shape)

    names, histories = load_tables(db, directory, tables, referenced)
    context = manifest.model_inputs.format == "in_context_examples"
    violation_count = sum(
        audit_path(db, manifest, [tables[name] for name in shape], names, histories, entries, context, smallest)
        for shape, entries in groups.items()
    )
    return len(items), violation_count
