"""Database backend abstraction — SQLite (local) or PostgreSQL/RDS, chosen by
STORAGE_BACKEND.

storage.py keeps writing queries in the SQLite style (``?`` placeholders,
``conn.execute(...).fetchone()``). This module makes those run unchanged on
PostgreSQL by:

- ``connect()`` returning a context-managed connection that commits + closes,
- translating ``?`` placeholders to ``%s`` for psycopg2,
- providing ``upsert()`` (INSERT OR REPLACE ↔ ON CONFLICT) and
  ``table_columns()`` (PRAGMA ↔ information_schema) helpers.

The backend must be selected explicitly. PostgreSQL is the documented local
development path; SQLite remains available for tests and lightweight demos.
"""
from __future__ import annotations

import os
import sqlite3

from app.services.config import DB_PATH, ensure_runtime_dirs


SUPPORTED_BACKENDS = {"sqlite", "postgres"}
POSTGRES_REQUIRED_ENV = ("RDS_HOST", "RDS_DB", "RDS_USER", "RDS_PASSWORD")


class DatabaseConfigurationError(RuntimeError):
    """Raised when the selected database backend is missing or incomplete."""


class DatabaseConnectionError(RuntimeError):
    """Raised when a configured database cannot be reached."""


def backend() -> str:
    value = os.environ.get("STORAGE_BACKEND", "").strip().lower()
    if not value:
        raise DatabaseConfigurationError(
            "STORAGE_BACKEND is required. Copy .env.example to .env for the "
            "PostgreSQL development setup, or set STORAGE_BACKEND=sqlite for "
            "the explicit lightweight demo/test path."
        )
    if value not in SUPPORTED_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise DatabaseConfigurationError(
            f"Unsupported STORAGE_BACKEND={value!r}. Expected one of: {supported}."
        )
    return value


def validate_configuration() -> str:
    """Validate backend selection and required PostgreSQL settings."""
    selected = backend()
    if selected == "postgres":
        missing = [name for name in POSTGRES_REQUIRED_ENV if not os.environ.get(name, "").strip()]
        if missing:
            raise DatabaseConfigurationError(
                "PostgreSQL configuration is incomplete. Missing: "
                f"{', '.join(missing)}. Copy .env.example to .env and verify RDS_* values."
            )
        try:
            port = int(os.environ.get("RDS_PORT", "5432"))
        except ValueError as exc:
            raise DatabaseConfigurationError("RDS_PORT must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise DatabaseConfigurationError("RDS_PORT must be between 1 and 65535.")
    return selected


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
class _SQLiteConn(sqlite3.Connection):
    """SQLite connection whose context manager also releases the file handle."""

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, tb))
        finally:
            self.close()


def _sqlite_conn() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(DB_PATH, factory=_SQLiteConn)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# PostgreSQL (psycopg2)
# ---------------------------------------------------------------------------
def _translate(sql: str) -> str:
    """SQLite SQL → psycopg2 SQL: escape literal % then ? → %s."""
    if "%" in sql:
        sql = sql.replace("%", "%%")
    return sql.replace("?", "%s")


def _translate_script(sql: str) -> str:
    """Schema DDL tweaks for PostgreSQL (e.g. BLOB → BYTEA)."""
    return sql.replace("BLOB", "BYTEA").replace("blob", "BYTEA")


class _PgConn:
    """Adapter giving a psycopg2 connection the small slice of the sqlite3
    Connection API that storage.py relies on (``execute``/``executescript`` +
    context-manager commit/close)."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor()
        if params:
            # Translate placeholders + escape literal % only when params are
            # bound (psycopg2 does %-interpolation only then). Parameterless
            # queries pass through verbatim so literal % (e.g. LIKE 'x%') stays.
            cur.execute(_translate(sql), params)
        else:
            cur.execute(sql)
        return cur

    def executemany(self, sql: str, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(_translate(sql), seq_of_params)
        return cur

    def executescript(self, sql: str):
        cur = self._conn.cursor()
        cur.execute(_translate_script(sql))
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


def _pg_conn() -> _PgConn:
    import psycopg2  # noqa: PLC0415
    import psycopg2.extras  # noqa: PLC0415

    validate_configuration()
    try:
        conn = psycopg2.connect(
            host=os.environ["RDS_HOST"],
            port=int(os.environ.get("RDS_PORT", "5432")),
            dbname=os.environ["RDS_DB"],
            user=os.environ["RDS_USER"],
            password=os.environ["RDS_PASSWORD"],
            cursor_factory=psycopg2.extras.RealDictCursor,
            connect_timeout=5,
        )
    except psycopg2.Error as exc:
        host = os.environ.get("RDS_HOST", "?")
        port = os.environ.get("RDS_PORT", "5432")
        database = os.environ.get("RDS_DB", "?")
        raise DatabaseConnectionError(
            f"PostgreSQL connection failed at {host}:{port}/{database}. "
            "Start it with 'docker compose up -d postgres' and verify RDS_* settings. "
            f"Driver detail: {str(exc).strip()}"
        ) from exc
    return _PgConn(conn)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def connect():
    """Return a connection usable as ``with connect() as conn: conn.execute(...)``.

    Both backends commit and close on successful context-manager exit and roll
    back and close when an exception escapes the block.
    """
    if validate_configuration() == "postgres":
        return _pg_conn()
    return _sqlite_conn()


def validate_connection() -> dict[str, str]:
    """Fail fast with an actionable message before application startup."""
    selected = validate_configuration()
    try:
        with connect() as conn:
            row = conn.execute("SELECT 1 AS ok").fetchone()
    except (DatabaseConfigurationError, DatabaseConnectionError):
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseConnectionError(
            f"{selected} database validation query failed: {exc}"
        ) from exc
    if row is None or int(row["ok"]) != 1:
        raise DatabaseConnectionError(f"{selected} database validation returned an unexpected result.")
    return {"backend": selected, "status": "connected"}


def upsert(conn, table: str, columns: list[str], values, conflict: str = "id") -> None:
    """Backend-aware INSERT-or-replace on a primary/unique key."""
    collist = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    if backend() == "postgres":
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in columns if c != conflict)
        sql = (
            f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
        )
    else:
        sql = f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})"
    conn.execute(sql, values)


def table_columns(conn, table: str) -> list[str]:
    """Existing column names of a table, in definition order (PRAGMA ↔
    information_schema)."""
    if backend() == "postgres":
        rows = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [row["name"] for row in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]
