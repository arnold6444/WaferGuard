"""Database backend abstraction for SQLite and PostgreSQL/RDS.

PostgreSQL is the standard development/production path. SQLite remains an
explicit lightweight demo/unit-test backend. Callers keep using the small
sqlite-style ``execute`` API while this module translates placeholders and
manages PostgreSQL connections through a thread-safe pool.
"""
from __future__ import annotations

import atexit
import os
import sqlite3
import threading
from typing import Any

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


def _pool_limits() -> tuple[int, int]:
    try:
        minimum = int(os.environ.get("DB_POOL_MIN", "1"))
        maximum = int(os.environ.get("DB_POOL_MAX", "10"))
    except ValueError as exc:
        raise DatabaseConfigurationError("DB_POOL_MIN and DB_POOL_MAX must be integers.") from exc
    if minimum < 1 or maximum < minimum:
        raise DatabaseConfigurationError("DB pool requires 1 <= DB_POOL_MIN <= DB_POOL_MAX.")
    return minimum, maximum


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
        _pool_limits()
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
# PostgreSQL (psycopg2 + ThreadedConnectionPool)
# ---------------------------------------------------------------------------
def _translate(sql: str) -> str:
    """SQLite SQL -> psycopg2 SQL: escape literal % then ? -> %s."""
    if "%" in sql:
        sql = sql.replace("%", "%%")
    return sql.replace("?", "%s")


def _translate_script(sql: str) -> str:
    """Schema DDL tweaks for PostgreSQL (e.g. BLOB -> BYTEA)."""
    return sql.replace("BLOB", "BYTEA").replace("blob", "BYTEA")


_PG_POOL: Any | None = None
_PG_POOL_KEY: tuple[object, ...] | None = None
_PG_POOL_LOCK = threading.RLock()


def _postgres_key() -> tuple[object, ...]:
    minimum, maximum = _pool_limits()
    return (
        os.environ.get("RDS_HOST"),
        int(os.environ.get("RDS_PORT", "5432")),
        os.environ.get("RDS_DB"),
        os.environ.get("RDS_USER"),
        os.environ.get("RDS_PASSWORD"),
        minimum,
        maximum,
    )


def close_postgres_pool() -> None:
    """Close all pooled PostgreSQL connections, primarily for shutdown/tests."""
    global _PG_POOL, _PG_POOL_KEY  # noqa: PLW0603
    with _PG_POOL_LOCK:
        pool = _PG_POOL
        _PG_POOL = None
        _PG_POOL_KEY = None
        if pool is not None:
            try:
                pool.closeall()
            except Exception:  # noqa: BLE001
                pass


atexit.register(close_postgres_pool)


def _postgres_pool():
    global _PG_POOL, _PG_POOL_KEY  # noqa: PLW0603
    import psycopg2.extras  # noqa: PLC0415
    from psycopg2.pool import ThreadedConnectionPool  # noqa: PLC0415

    validate_configuration()
    key = _postgres_key()
    with _PG_POOL_LOCK:
        if _PG_POOL is not None and _PG_POOL_KEY == key:
            return _PG_POOL
        if _PG_POOL is not None:
            try:
                _PG_POOL.closeall()
            except Exception:  # noqa: BLE001
                pass
        host, port, database, user, password, minimum, maximum = key
        try:
            _PG_POOL = ThreadedConnectionPool(
                int(minimum),
                int(maximum),
                host=host,
                port=int(port),
                dbname=database,
                user=user,
                password=password,
                cursor_factory=psycopg2.extras.RealDictCursor,
                connect_timeout=5,
            )
        except Exception as exc:  # psycopg2 errors are imported lazily
            _PG_POOL = None
            _PG_POOL_KEY = None
            raise DatabaseConnectionError(
                f"PostgreSQL connection pool failed at {host}:{port}/{database}. "
                "Start it with 'docker compose up -d postgres' and verify RDS_* settings. "
                f"Driver detail: {str(exc).strip()}"
            ) from exc
        _PG_POOL_KEY = key
        return _PG_POOL


class _PgConn:
    """Small sqlite-like adapter around one borrowed PostgreSQL connection."""

    def __init__(self, conn, pool) -> None:
        self._conn = conn
        self._pool = pool
        self._released = False

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor()
        if params:
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

    def _release(self, *, broken: bool = False) -> None:
        if self._released:
            return
        self._released = True
        self._pool.putconn(self._conn, close=broken or bool(getattr(self._conn, "closed", False)))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        broken = False
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except Exception:  # noqa: BLE001
            broken = True
            raise
        finally:
            self._release(broken=broken)
        return False


def _pg_conn() -> _PgConn:
    pool = _postgres_pool()
    try:
        conn = pool.getconn()
    except Exception as exc:  # noqa: BLE001
        close_postgres_pool()
        host = os.environ.get("RDS_HOST", "?")
        port = os.environ.get("RDS_PORT", "5432")
        database = os.environ.get("RDS_DB", "?")
        raise DatabaseConnectionError(
            f"PostgreSQL pool checkout failed at {host}:{port}/{database}: {exc}"
        ) from exc
    return _PgConn(conn, pool)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def connect():
    """Return a context-managed SQLite connection or pooled PostgreSQL adapter."""
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
    """Existing column names in definition order (PRAGMA <-> information_schema)."""
    if backend() == "postgres":
        rows = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [row["name"] for row in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]
