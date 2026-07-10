from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from .migrations import initialize_schema, require_current_schema


class DatabaseOpenError(RuntimeError):
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        super().__init__("file could not be opened as a DuckDB database")


def open_database(
    database_path: Path,
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    try:
        return duckdb.connect(str(database_path), read_only=read_only)
    except duckdb.Error as exc:
        raise DatabaseOpenError(database_path) from exc


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with open_database(database_path) as connection:
        initialize_schema(connection)


@contextmanager
def write_connection(database_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with open_database(database_path) as connection:
        initialize_schema(connection)
        yield connection


@contextmanager
def read_connection(database_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    with open_database(database_path, read_only=True) as connection:
        require_current_schema(connection)
        yield connection


@contextmanager
def inspection_connection(database_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    with open_database(database_path, read_only=True) as connection:
        yield connection


@contextmanager
def transaction(connection: duckdb.DuckDBPyConnection) -> Iterator[None]:
    connection.execute("BEGIN TRANSACTION")
    try:
        yield
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
