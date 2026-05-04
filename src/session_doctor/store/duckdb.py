from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from .migrations import SCHEMA_VERSION, TABLE_NAMES, apply_migrations


@dataclass(frozen=True)
class StoreInfo:
    database_path: Path
    exists: bool
    schema_version: int | None
    tables: tuple[str, ...]


class DuckDBStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser()

    def initialize(self) -> StoreInfo:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.database_path)) as connection:
            apply_migrations(connection)
        return self.info()

    def info(self) -> StoreInfo:
        if not self.database_path.exists():
            return StoreInfo(
                database_path=self.database_path,
                exists=False,
                schema_version=None,
                tables=(),
            )

        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            tables = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                    ORDER BY table_name
                    """
                ).fetchall()
            )
            schema_version = self._schema_version(connection, tables)

        return StoreInfo(
            database_path=self.database_path,
            exists=True,
            schema_version=schema_version,
            tables=tables,
        )

    @staticmethod
    def _schema_version(
        connection: duckdb.DuckDBPyConnection,
        tables: tuple[str, ...],
    ) -> int | None:
        if "schema_migrations" not in tables:
            return None
        row = connection.execute(
            "SELECT MAX(version) FROM schema_migrations",
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def expected_table_count() -> int:
    return len(TABLE_NAMES)


def current_schema_version() -> int:
    return SCHEMA_VERSION

