"""Typed results of the main read tools, so the MCP SDK publishes an outputSchema for them.

Naming convention: keys that Nexus returns keep its PascalCase (Total, Rows, Items...). Every field
this server ADDS is lowercase snake_case (next_offset). All fields are optional so a different Nexus
version cannot make a valid answer fail validation.
"""

from typing import Any

from typing_extensions import TypedDict


class QueryResult(TypedDict, total=False):
    # compact shape
    total: int | None
    offset: int | None
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    next_offset: int | None  # offset to pass for the next page, null when there is no more
    # non-compact (Nexus) shape
    Total: int | None
    Offset: int | None
    Limit: int | None
    Columns: list[str]
    Rows: list[dict[str, Any]]
    Truncated: bool


class CountResult(TypedDict):
    total: int | None


class TreeResult(TypedDict, total=False):
    Root: str
    Items: list[dict[str, Any]]
    Truncated: bool


class ChildSummary(TypedDict, total=False):
    total: int
    by_type: dict[str, int]
    truncated: bool


class DescribeResult(TypedDict, total=False):
    dbref: str
    type: str | None
    name: str | None
    owner: str | None
    attributes: dict[str, Any]
    children: ChildSummary
