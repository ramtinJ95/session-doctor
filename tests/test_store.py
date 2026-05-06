from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from session_doctor.adapters.codex import CodexAdapter
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, SessionSource
from session_doctor.store import SCHEMA_VERSION, TABLE_NAMES, DuckDBStore
from session_doctor.store.migrations import apply_migrations

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"


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


def test_store_insert_parsed_bundle_persists_normalized_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_parsed_bundle(source, bundle)

    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("raw_events") == 11
    assert store.table_count("messages") == 2
    assert store.table_count("tool_calls") == 1
    assert store.table_count("tool_results") == 1
    assert store.table_count("command_runs") == 1
    assert store.table_count("file_activities") == 1
    assert store.table_count("parse_warnings") == 2


def test_store_insert_parsed_bundle_replaces_existing_source_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_parsed_bundle(source, bundle)
    store.insert_parsed_bundle(source, bundle)

    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("raw_events") == 11
    assert store.table_count("messages") == 2
    assert store.table_count("parse_warnings") == 2


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, path),
        agent_name=AgentName.CODEX,
        source_path=str(path),
    )
