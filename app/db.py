"""
Database access layer.

Deliberately thin: one psycopg connection per request, plain SQL strings,
no ORM and no query builder.  Every statement in the application is a
parameterised raw query, which is the project's hard requirement and the
main defence against SQL injection.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row
from flask import current_app, g


def dsn() -> str:
    """Connection string from DATABASE_URL or the local development defaults."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return (
        f"postgresql://{os.environ.get('PGUSER', 'rescue_admin')}:"
        f"{os.environ.get('PGPASSWORD', 'rescue_dev_pw')}@"
        f"{os.environ.get('PGHOST', '127.0.0.1')}:"
        f"{os.environ.get('PGPORT', '5433')}/"
        f"{os.environ.get('PGDATABASE', 'rescuelink')}"
    )


def get_db() -> psycopg.Connection:
    """Request-scoped connection with dict rows (templates get named columns)."""
    if "db" not in g:
        g.db = psycopg.connect(dsn(), row_factory=dict_row)
        g.db.autocommit = False
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is None:
        return
    if db.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        db.rollback()
    db.close()


def query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    cur = get_db().execute(sql, params)
    return cur.fetchall()


def query_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    return get_db().execute(sql, params).fetchone()


def execute(sql: str, params: tuple | dict | None = None) -> int:
    """Run a single write and commit; returns affected row count."""
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur.rowcount


def init_app(app) -> None:
    app.teardown_appcontext(close_db)


__all__ = ["dsn", "get_db", "init_app", "query", "query_one", "execute"]
