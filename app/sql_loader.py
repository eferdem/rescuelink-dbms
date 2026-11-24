"""
Loader for the analytical query suite.

sql/04_queries.sql is the single source of truth: the report, the
`psql -f` demo and the web application all read the same text, so the
SQL shown on the analytics page can never drift away from the SQL that
was benchmarked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

QUERY_MARKER = re.compile(r"^-- @query:\s*(\S+)\s*$")
META = re.compile(r"^-- @(\w+):\s*(.*)$")


@dataclass
class Query:
    name: str
    title: str = ""
    techniques: str = ""
    description: str = ""
    sql: str = field(default="", repr=False)


def load_queries(path: str | Path) -> list[Query]:
    text = Path(path).read_text(encoding="utf-8")
    queries: list[Query] = []
    current: Query | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            current.sql = "\n".join(body).strip()
            queries.append(current)
        body = []

    for line in text.splitlines():
        marker = QUERY_MARKER.match(line)
        if marker:
            flush()
            current = Query(name=marker.group(1))
            continue
        if current is None:
            continue
        meta = META.match(line)
        if meta and not current.sql:
            key, value = meta.group(1), meta.group(2)
            if key in {"title", "techniques", "description"}:
                setattr(current, key, value)
            continue
        body.append(line)

    flush()
    return queries


if __name__ == "__main__":
    for q in load_queries(Path(__file__).resolve().parents[1] / "sql" / "04_queries.sql"):
        print(f"{q.name:<26} {q.title}")
