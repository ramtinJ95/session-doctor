from __future__ import annotations

import duckdb
import pytest

from session_doctor.store import SCHEMA_VERSION, TABLE_NAMES, DuckDBStore
from session_doctor.store.migrations import apply_migrations


def test_store_initialize_creates_expected_tables(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)

    info = store.initialize()

    assert info.exists
    assert info.schema_version == SCHEMA_VERSION
    assert set(TABLE_NAMES).issubset(set(info.tables))


def test_store_info_handles_missing_database(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "missing.duckdb")

    info = store.info()

    assert info.exists is False
    assert info.schema_version is None
    assert info.tables == ()


def test_migration_rejects_newer_schema(tmp_path) -> None:
    database_path = tmp_path / "newer.duckdb"
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            [SCHEMA_VERSION + 1],
        )
        with pytest.raises(RuntimeError, match="newer"):
            apply_migrations(connection)
