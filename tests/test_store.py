from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.claude import ClaudeCodeAdapter
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    ModelUsage,
    NormalizedRole,
    RawEvent,
    Session,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import (
    DURABLE_TABLE_NAMES,
    SCHEMA_VERSION,
    TABLE_NAMES,
    CaptureProvenanceError,
    DuckDBStore,
    StaleCaptureError,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"
CLAUDE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claude"


def test_store_initialize_creates_expected_tables(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)

    info = store.initialize()

    assert info.exists
    assert info.schema_version == SCHEMA_VERSION
    assert set(TABLE_NAMES).issubset(set(info.tables))
    assert "graph_nodes" not in info.tables
    assert "graph_edges" not in info.tables
    assert set(DURABLE_TABLE_NAMES) == {
        "source_blobs",
        "logical_sources",
        "source_snapshots",
        "snapshot_bundles",
        "snapshot_bundle_members",
        "bundle_capture_metadata",
        "bundle_member_capture_metadata",
        "lifecycle_observations",
        "evaluation_packets",
        "evaluation_corpora",
        "judge_annotations",
        "judge_panel_resolutions",
        "audit_selections",
        "audit_protocols",
        "human_adjudications",
        "reference_resolutions",
    }


def test_store_info_handles_missing_database(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "missing.duckdb")

    info = store.info()

    assert info.exists is False
    assert info.schema_version is None
    assert info.tables == ()


def test_store_initialize_records_current_internal_schema_version(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)

    store.initialize()

    with duckdb.connect(str(database_path)) as connection:
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()

    assert row == (SCHEMA_VERSION,)


def test_source_snapshots_round_trip_exact_bytes_and_deduplicate_blobs(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/sessions/source-1.jsonl",
    )

    first = store.capture_source(source, b'{"type":"first"}\n\x00')
    second = store.capture_source(source, b'{"type":"first"}\n\x00')
    third = store.capture_source(source, b'{"type":"first"}\n{"type":"second"}\n')
    for captured in (first, second, third):
        store.create_single_source_bundle(source, captured, "native-session-1")

    assert first.blob_id == second.blob_id
    assert first.snapshot_content_id == second.snapshot_content_id
    assert first.snapshot_id != second.snapshot_id
    assert second.snapshot_id != third.snapshot_id
    assert second.capture_sequence == 2
    assert third.capture_sequence == 3
    assert store.load_snapshot_bytes(first.snapshot_id) == b'{"type":"first"}\n\x00'
    assert store.load_snapshot_bytes(third.snapshot_id) == (
        b'{"type":"first"}\n{"type":"second"}\n'
    )

    with duckdb.connect(str(database_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM source_blobs").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM source_snapshots").fetchone() == (3,)
        assert connection.execute("SELECT count(*) FROM snapshot_bundles").fetchone() == (3,)
        assert connection.execute("SELECT count(*) FROM snapshot_bundle_members").fetchone() == (3,)


def test_schema_rebuild_preserves_durable_snapshots(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.PI,
        source_path="/sessions/source-1.jsonl",
    )
    captured = store.capture_source(source, b"exact history")
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = ?", [SCHEMA_VERSION])
        connection.execute("INSERT INTO schema_migrations (version) VALUES (4)")

    store.initialize()

    assert store.load_snapshot_bytes(captured.snapshot_id) == b"exact history"
    assert store.info().schema_version == SCHEMA_VERSION


def test_older_capture_cannot_replace_newer_normalized_projection(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/sessions/source-1.jsonl",
    )
    older = store.capture_source(source, b"older")
    older_bundle = store.create_single_source_bundle(source, older, source.source_id)
    store.capture_source(source, b"newer")

    with pytest.raises(StaleCaptureError, match="no longer the latest"):
        store.insert_parsed_bundle(
            source,
            ParsedSessionBundle(),
            older,
            older_bundle,
        )


def test_projection_rejects_capture_from_another_source(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    captured_source = SessionSource(
        source_id="captured-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/captured.jsonl",
    )
    other_source = captured_source.model_copy(update={"source_id": "other-source"})
    captured = store.capture_source(captured_source, b"captured")
    captured_bundle = store.create_single_source_bundle(captured_source, captured, "native-1")

    with pytest.raises(CaptureProvenanceError, match="does not belong"):
        store.insert_parsed_bundle(
            other_source,
            ParsedSessionBundle(),
            captured,
            captured_bundle,
        )

    forged_bundle = replace(captured_bundle, native_session_identity="other-native")
    with pytest.raises(CaptureProvenanceError, match="does not belong"):
        store.insert_parsed_bundle(
            captured_source,
            ParsedSessionBundle(),
            captured,
            forged_bundle,
        )

    wrong_session = Session(
        session_id="session-1",
        source_id="other-source",
        agent_name=captured_source.agent_name,
    )
    with pytest.raises(CaptureProvenanceError, match="does not belong"):
        store.insert_parsed_bundle(
            captured_source,
            ParsedSessionBundle(session=wrong_session),
            captured,
            captured_bundle,
        )


def test_snapshot_manifest_constraints_reject_empty_captured_members(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    DuckDBStore(database_path).initialize()
    with duckdb.connect(str(database_path)) as connection:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                """
                INSERT INTO snapshot_bundle_members (
                    snapshot_bundle_id, logical_source_id, snapshot_id,
                    capture_order, member_role, member_capture_status
                ) VALUES ('missing-bundle', 'missing-source', NULL, 0, 'primary', 'captured')
                """
            )


def test_store_insert_parsed_bundle_persists_normalized_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_live_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_untracked_parsed_bundle(source, bundle)

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
    bundle = CodexAdapter().parse_live_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_untracked_parsed_bundle(source, bundle)

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


def test_store_insert_parsed_bundle_normalizes_offset_aware_timestamps(tmp_path) -> None:
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/tmp/source.jsonl",
    )
    timestamp = datetime(2026, 5, 6, 1, 0, tzinfo=timezone(timedelta(hours=-7)))
    bundle = ParsedSessionBundle(
        session=Session(
            session_id="session-1",
            source_id=source.source_id,
            agent_name=AgentName.CODEX,
            started_at=timestamp,
        ),
        raw_events=[
            RawEvent(
                event_id="event-1",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=1,
                timestamp=timestamp,
            )
        ],
        messages=[
            Message(
                message_id="message-1",
                session_id="session-1",
                role=NormalizedRole.USER,
                source_event_id="event-1",
                timestamp=timestamp,
                text="Offset timestamp message.",
                text_length=len("Offset timestamp message."),
            )
        ],
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_untracked_parsed_bundle(source, bundle)

    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        session_started_at = connection.execute(
            "SELECT CAST(started_at AS VARCHAR) FROM sessions"
        ).fetchone()
        event_timestamp = connection.execute(
            "SELECT CAST(timestamp AS VARCHAR) FROM raw_events"
        ).fetchone()
        message_timestamp = connection.execute(
            "SELECT CAST(timestamp AS VARCHAR) FROM messages"
        ).fetchone()

    assert session_started_at == ("2026-05-06 08:00:00",)
    assert event_timestamp == ("2026-05-06 08:00:00",)
    assert message_timestamp == ("2026-05-06 08:00:00",)


def test_store_insert_parsed_bundle_replaces_existing_source_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_live_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_untracked_parsed_bundle(source, bundle)
    store.insert_untracked_parsed_bundle(source, bundle)

    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("raw_events") == 17
    assert store.table_count("messages") == 2
    assert store.table_count("parse_warnings") == 2


def test_store_list_session_summaries_includes_message_source_counts(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_live_source(source)
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)

    summaries = store.list_session_summaries()

    assert len(summaries) == 1
    assert summaries[0].message_count == 2
    assert summaries[0].response_item_message_count == 2
    assert summaries[0].event_msg_fallback_count == 0
    assert summaries[0].source_path == str(fixture_path)
    assert store.list_session_summaries("codex") == summaries
    assert store.list_session_summaries("pi") == ()
    assert store.session_agent_name(summaries[0].session_id) == "codex"
    assert store.session_agent_name("missing") is None


def test_store_load_session_bundle_round_trips_ingested_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_live_source(source)
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)

    loaded = store.load_session_bundle(bundle.session.session_id)

    assert loaded is not None
    assert loaded.session is not None
    assert loaded.session.session_id == bundle.session.session_id
    assert len(loaded.raw_events) == len(bundle.raw_events)
    assert len(loaded.messages) == len(bundle.messages)
    assert len(loaded.command_runs) == len(bundle.command_runs)
    assert loaded.messages[0].content_block_types == ["input_text"]
    assert loaded.messages[0].metadata["codex_message_source"] == "response_item"


def test_store_round_trips_claude_root_records_without_private_payloads(tmp_path) -> None:
    fixture_path = CLAUDE_FIXTURE_DIR / "basic-session.jsonl"
    source = SessionSource(
        source_id=source_id_for_path(AgentName.CLAUDE, fixture_path),
        agent_name=AgentName.CLAUDE,
        source_path=str(fixture_path),
    )
    bundle = ClaudeCodeAdapter().parse_live_source(source)
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_untracked_parsed_bundle(source, bundle)
    loaded = store.load_session_bundle(bundle.session.session_id)

    assert loaded is not None
    assert loaded.session is not None
    assert loaded.session.agent_name is AgentName.CLAUDE
    assert len(loaded.raw_events) == 9
    assert len(loaded.messages) == 6
    assert len(loaded.tool_calls) == 5
    assert len(loaded.tool_results) == 2
    assert len(loaded.command_runs) == 1
    assert len(loaded.file_activities) == 3
    assert len(loaded.model_usage) == 2
    serialized_bundle = loaded.model_dump_json()
    assert "PRIVATE_THINKING_TEXT" not in serialized_bundle
    assert "PRIVATE_COMMAND_OUTPUT" not in serialized_bundle
    assert "PRIVATE_WRITE_BODY" not in serialized_bundle
    assert "PRIVATE_PATCH" not in serialized_bundle


def test_store_load_session_bundle_orders_messages_by_raw_event_index(tmp_path) -> None:
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/tmp/source.jsonl",
    )
    session = Session(
        session_id="session-1", source_id=source.source_id, agent_name=AgentName.CODEX
    )
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id="event-1",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=1,
            ),
            RawEvent(
                event_id="event-2",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=2,
            ),
        ],
        messages=[
            Message(
                message_id="z-message",
                session_id=session.session_id,
                role=NormalizedRole.USER,
                source_event_id="event-1",
                text="Original request.",
                text_length=len("Original request."),
            ),
            Message(
                message_id="a-message",
                session_id=session.session_id,
                role=NormalizedRole.USER,
                source_event_id="event-2",
                text="Repeated request.",
                text_length=len("Repeated request."),
            ),
        ],
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)

    loaded = store.load_session_bundle(session.session_id)

    assert loaded is not None
    assert [message.message_id for message in loaded.messages] == ["z-message", "a-message"]


def test_store_load_session_bundle_orders_analysis_records_by_raw_event_index(tmp_path) -> None:
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/tmp/source.jsonl",
    )
    session = Session(
        session_id="session-1", source_id=source.source_id, agent_name=AgentName.CODEX
    )
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id="event-1",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=1,
            ),
            RawEvent(
                event_id="event-2",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=2,
            ),
        ],
        tool_calls=[
            ToolCall(
                tool_call_id="z-tool-call",
                session_id=session.session_id,
                source_event_id="event-1",
                name="shell",
            ),
            ToolCall(
                tool_call_id="a-tool-call",
                session_id=session.session_id,
                source_event_id="event-2",
                name="shell",
            ),
        ],
        tool_results=[
            ToolResult(
                tool_result_id="z-tool-result",
                session_id=session.session_id,
                source_event_id="event-1",
            ),
            ToolResult(
                tool_result_id="a-tool-result",
                session_id=session.session_id,
                source_event_id="event-2",
            ),
        ],
        command_runs=[
            CommandRun(
                command_run_id="z-command",
                session_id=session.session_id,
                source_event_id="event-1",
                command="first",
            ),
            CommandRun(
                command_run_id="a-command",
                session_id=session.session_id,
                source_event_id="event-2",
                command="second",
            ),
        ],
        file_activities=[
            FileActivity(
                file_activity_id="z-file",
                session_id=session.session_id,
                source_event_id="event-1",
                path="first.py",
                operation="edit",
            ),
            FileActivity(
                file_activity_id="a-file",
                session_id=session.session_id,
                source_event_id="event-2",
                path="second.py",
                operation="edit",
            ),
        ],
        model_usage=[
            ModelUsage(
                model_usage_id="z-usage",
                session_id=session.session_id,
                source_event_id="event-1",
            ),
            ModelUsage(
                model_usage_id="a-usage",
                session_id=session.session_id,
                source_event_id="event-2",
            ),
        ],
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)

    loaded = store.load_session_bundle(session.session_id)

    assert loaded is not None
    assert [tool_call.tool_call_id for tool_call in loaded.tool_calls] == [
        "z-tool-call",
        "a-tool-call",
    ]
    assert [tool_result.tool_result_id for tool_result in loaded.tool_results] == [
        "z-tool-result",
        "a-tool-result",
    ]
    assert [command.command_run_id for command in loaded.command_runs] == [
        "z-command",
        "a-command",
    ]
    assert [activity.file_activity_id for activity in loaded.file_activities] == [
        "z-file",
        "a-file",
    ]
    assert [usage.model_usage_id for usage in loaded.model_usage] == ["z-usage", "a-usage"]


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, path),
        agent_name=AgentName.CODEX,
        source_path=str(path),
    )
