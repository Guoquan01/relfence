from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


Name = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[a-fA-F0-9]{64}$")]


class Record(BaseModel):
    model_config = ConfigDict(strict=True)


class ForeignKey(Record):
    column: Name
    references_table: Name
    references_column: Name


class Table(Record):
    name: Name
    uri: Name
    sha256: Digest
    primary_key: Name
    available_at_col: Name
    foreign_keys: list[ForeignKey]
    is_mutable: bool
    valid_from_col: Name | None = None
    valid_to_col: Name | None = None


class Task(Record):
    task_id: Name
    task_type: Literal["classification", "regression", "ranking"]
    entity_table: Name
    entity_pk: Name
    prediction_cutoff_col: Name
    label_window_end_col: Name


class Inputs(Record):
    format: Literal["graph_subgraph", "in_context_examples"]
    uri: Name
    sha256: Digest
    lineage_map_uri: Name
    lineage_map_sha256: Digest


class Policy(Record):
    strict_inequality: bool = True
    timezone: Literal["UTC"] = "UTC"


class Manifest(Record):
    tables: Annotated[list[Table], Field(min_length=1)]
    task: Task
    model_inputs: Inputs
    policy: Policy = Field(default_factory=Policy)


class Item(Record):
    model_config = ConfigDict(strict=True, extra="allow")
    prediction_id: Name
    item_id: Name
    entity_id: Name


class Hop(Record):
    table: Name
    pk: Name
    valid_from: Name | None = None


class Lineage(Record):
    prediction_id: Name
    item_id: Name
    join_path: Annotated[list[Hop], Field(min_length=1)]
