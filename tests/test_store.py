from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from session_doctor.adapters.codex import CodexAdapter
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    MessageFeature,
    SessionClassification,
    SessionFeature,
    SessionSource,
)
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
    assert store.table_count("raw_events") == 17
    assert store.table_count("messages") == 2
    assert store.table_count("tool_calls") == 2
    assert store.table_count("tool_results") == 2
    assert store.table_count("command_runs") == 1
    assert store.table_count("file_activities") == 1
    assert store.table_count("model_usage") == 1
    assert store.table_count("parse_warnings") == 2


def test_store_insert_parsed_bundle_preserves_utc_timestamps(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_parsed_bundle(source, bundle)

    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        session_started_at = connection.execute(
            "SELECT CAST(started_at AS VARCHAR) FROM sessions"
        ).fetchone()
        first_event_timestamp = connection.execute(
            """
            SELECT CAST(timestamp AS VARCHAR)
            FROM raw_events
            ORDER BY record_index
            LIMIT 1
            """
        ).fetchone()

    assert session_started_at == ("2026-05-06 08:00:00",)
    assert first_event_timestamp == ("2026-05-06 08:00:00",)


def test_store_insert_parsed_bundle_replaces_existing_source_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_parsed_bundle(source, bundle)
    store.insert_parsed_bundle(source, bundle)

    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("raw_events") == 17
    assert store.table_count("messages") == 2
    assert store.table_count("parse_warnings") == 2


def test_store_list_session_summaries_includes_message_source_counts(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_parsed_bundle(source, bundle)

    summaries = store.list_session_summaries()

    assert len(summaries) == 1
    assert summaries[0].message_count == 2
    assert summaries[0].response_item_message_count == 2
    assert summaries[0].event_msg_fallback_count == 0
    assert summaries[0].source_path == str(fixture_path)


def test_store_replace_analysis_rows_rebuilds_derived_records(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    analysis_run = AnalysisRun(
        analysis_run_id="analysis-1",
        session_id="session-1",
        analyzer_version="phase3",
        artifact_path="/tmp/session-1-analysis.json",
    )
    message_feature = MessageFeature(
        message_feature_id="message-feature-1",
        analysis_run_id=analysis_run.analysis_run_id,
        session_id=analysis_run.session_id,
        message_id="message-1",
        feature_name="correction_marker",
        feature_value="true",
    )
    session_feature = SessionFeature(
        session_feature_id="session-feature-1",
        analysis_run_id=analysis_run.analysis_run_id,
        session_id=analysis_run.session_id,
        feature_name="correction_count",
        feature_value="1",
    )
    classification = SessionClassification(
        session_classification_id="classification-1",
        analysis_run_id=analysis_run.analysis_run_id,
        session_id=analysis_run.session_id,
        label="user_stuck",
        score=0.8,
        confidence=0.7,
        evidence_event_ids=["event-1"],
        evidence_summary="Repeated request and correction evidence.",
    )

    store.replace_analysis_rows(
        analysis_run,
        [message_feature],
        [session_feature],
        [classification],
    )

    replacement_run = AnalysisRun(
        analysis_run_id="analysis-2",
        session_id="session-1",
        analyzer_version="phase3",
        artifact_path="/tmp/session-1-analysis-v2.json",
    )
    replacement_feature = SessionFeature(
        session_feature_id="session-feature-2",
        analysis_run_id=replacement_run.analysis_run_id,
        session_id=replacement_run.session_id,
        feature_name="correction_count",
        feature_value="2",
    )

    store.replace_analysis_rows(replacement_run, [], [replacement_feature], [])

    assert store.table_count("analysis_runs") == 1
    assert store.table_count("message_features") == 0
    assert store.table_count("session_features") == 1
    assert store.table_count("session_classifications") == 0

    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        row = connection.execute(
            """
            SELECT analysis_run_id, artifact_path
            FROM analysis_runs
            """
        ).fetchone()
        feature_row = connection.execute(
            """
            SELECT feature_name, feature_value
            FROM session_features
            """
        ).fetchone()

    assert row == ("analysis-2", "/tmp/session-1-analysis-v2.json")
    assert feature_row == ("correction_count", "2")


def test_store_insert_parsed_bundle_deletes_existing_analysis_rows(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_parsed_bundle(source, bundle)
    assert bundle.session is not None

    analysis_run = AnalysisRun(
        analysis_run_id="analysis-1",
        session_id=bundle.session.session_id,
        analyzer_version="phase3",
    )
    session_feature = SessionFeature(
        session_feature_id="session-feature-1",
        analysis_run_id=analysis_run.analysis_run_id,
        session_id=bundle.session.session_id,
        feature_name="correction_count",
        feature_value="1",
    )
    store.replace_analysis_rows(analysis_run, [], [session_feature], [])

    store.insert_parsed_bundle(source, bundle)

    assert store.table_count("analysis_runs") == 0
    assert store.table_count("session_features") == 0


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, path),
        agent_name=AgentName.CODEX,
        source_path=str(path),
    )
