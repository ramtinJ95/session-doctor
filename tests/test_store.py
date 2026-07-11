from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.claude import ClaudeCodeAdapter
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.analysis import ANALYZER_VERSION
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    CommandRun,
    FileActivity,
    Message,
    MessageFeature,
    ModelUsage,
    NormalizedRole,
    RawEvent,
    Session,
    SessionClassification,
    SessionFeature,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import (
    SCHEMA_VERSION,
    TABLE_NAMES,
    AnalysisCompatibility,
    DuckDBStore,
    SchemaMismatchError,
    SessionScopeFilters,
    SummaryFilters,
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


def test_store_classifies_filtered_analysis_targets_and_orders_untimed_last(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    sessions = (
        Session(
            session_id="session-current",
            source_id="source-current",
            agent_name=AgentName.CODEX,
            project_path="/work/project",
            started_at=datetime(2026, 1, 1, 8, 0),
        ),
        Session(
            session_id="session-stale",
            source_id="source-stale",
            agent_name=AgentName.CODEX,
            project_path="/work/project/subdir",
            started_at=datetime(2026, 1, 2, 8, 0),
        ),
        Session(
            session_id="session-missing",
            source_id="source-missing",
            agent_name=AgentName.CODEX,
            project_path="/work/project",
        ),
        Session(
            session_id="session-other-agent",
            source_id="source-other-agent",
            agent_name=AgentName.PI,
            project_path="/work/project",
        ),
    )
    for session in sessions:
        source = SessionSource(
            source_id=session.source_id,
            agent_name=session.agent_name,
            source_path=f"/tmp/{session.source_id}.jsonl",
        )
        store.insert_parsed_bundle(source, ParsedSessionBundle(session=session))

    for session_id, version in (
        ("session-current", ANALYZER_VERSION),
        ("session-stale", "phase5"),
    ):
        store.replace_analysis_rows(
            AnalysisRun(
                analysis_run_id=f"analysis-{session_id}",
                session_id=session_id,
                analyzer_version=version,
                started_at=datetime(2026, 1, 3, 8, 0),
                completed_at=datetime(2026, 1, 3, 8, 1),
            ),
            [],
            [],
            [],
        )

    targets = store.list_analysis_targets(
        SessionScopeFilters(agent_name="codex", project_path="/work/project")
    )

    assert [(target.session_id, target.compatibility) for target in targets] == [
        ("session-current", AnalysisCompatibility.CURRENT),
        ("session-stale", AnalysisCompatibility.STALE),
        ("session-missing", AnalysisCompatibility.MISSING),
    ]
    assert targets[0].analyzer_version == ANALYZER_VERSION
    assert targets[1].analyzer_version == "phase5"
    assert targets[2].analyzer_version is None


def test_store_project_root_scope_includes_absolute_paths(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    session = Session(
        session_id="session-root-scope",
        source_id="source-root-scope",
        agent_name=AgentName.CODEX,
        project_path="/work/project",
    )
    source = SessionSource(
        source_id=session.source_id,
        agent_name=session.agent_name,
        source_path="/tmp/root-scope.jsonl",
    )
    store.insert_parsed_bundle(source, ParsedSessionBundle(session=session))

    targets = store.list_analysis_targets(SessionScopeFilters(project_path="/"))

    assert [target.session_id for target in targets] == ["session-root-scope"]


def test_store_initialize_rejects_stale_schema_without_modifying_it(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (2)")

    store = DuckDBStore(database_path)

    with pytest.raises(SchemaMismatchError, match="version is 2; expected 4"):
        store.initialize()
    with pytest.raises(SchemaMismatchError, match="Rebuild the database"):
        store.aggregate_summary(SummaryFilters())

    with duckdb.connect(str(database_path), read_only=True) as connection:
        version_row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()

    assert store.info().schema_version == 2
    assert version_row == (2,)
    assert tables == [("schema_migrations",)]


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

    store.insert_parsed_bundle(source, bundle)

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
    assert store.list_session_summaries("codex") == summaries
    assert store.list_session_summaries("pi") == ()


def test_store_load_session_bundle_round_trips_ingested_records(tmp_path) -> None:
    fixture_path = FIXTURE_DIR / "basic-session.jsonl"
    source = source_for_fixture(fixture_path)
    bundle = CodexAdapter().parse_source(source)
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_parsed_bundle(source, bundle)

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
    bundle = ClaudeCodeAdapter().parse_source(source)
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")

    store.insert_parsed_bundle(source, bundle)
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
    store.insert_parsed_bundle(source, bundle)

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
    store.insert_parsed_bundle(source, bundle)

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


def test_store_aggregate_summary_counts_sessions_and_analysis(tmp_path) -> None:
    codex_path = FIXTURE_DIR / "repeated-failure-session.jsonl"
    pi_path = Path(__file__).parent / "fixtures" / "pi" / "repeated-failure-session.jsonl"
    claude_path = CLAUDE_FIXTURE_DIR / "repeated-failure-session.jsonl"
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    codex_source = source_for_fixture(codex_path)
    pi_source = SessionSource(
        source_id=source_id_for_path(AgentName.PI, pi_path),
        agent_name=AgentName.PI,
        source_path=str(pi_path),
    )
    claude_source = SessionSource(
        source_id=source_id_for_path(AgentName.CLAUDE, claude_path),
        agent_name=AgentName.CLAUDE,
        source_path=str(claude_path),
    )
    codex_bundle = CodexAdapter().parse_source(codex_source)
    from session_doctor.adapters.pi import PiAdapter

    pi_bundle = PiAdapter().parse_source(pi_source)
    claude_bundle = ClaudeCodeAdapter().parse_source(claude_source)
    assert codex_bundle.session is not None
    assert pi_bundle.session is not None
    assert claude_bundle.session is not None
    store.insert_parsed_bundle(codex_source, codex_bundle)
    store.insert_parsed_bundle(pi_source, pi_bundle)
    store.insert_parsed_bundle(claude_source, claude_bundle)

    initial_summary = store.aggregate_summary(SummaryFilters())

    assert initial_summary.total_sessions == 3
    assert initial_summary.analyzed_sessions == 0
    assert initial_summary.unanalyzed_sessions == 3
    assert {row.agent_name for row in initial_summary.agent_counts} == {
        "claude",
        "codex",
        "pi",
    }

    add_summary_analysis_rows(store, codex_bundle.session.session_id, "user_stuck", 0.8)
    add_summary_analysis_rows(store, pi_bundle.session.session_id, "tooling_blocked", 0.7)
    add_summary_analysis_rows(store, claude_bundle.session.session_id, "agent_looping", 0.6)

    summary = store.aggregate_summary(SummaryFilters())

    assert summary.total_sessions == 3
    assert summary.analyzed_sessions == 3
    assert summary.unanalyzed_sessions == 0
    assert {row.label: row.session_count for row in summary.classification_counts} == {
        "agent_looping": 1,
        "tooling_blocked": 1,
        "user_stuck": 1,
    }
    assert [row.session_id for row in summary.recent_risk_sessions] == [
        codex_bundle.session.session_id,
        pi_bundle.session.session_id,
        claude_bundle.session.session_id,
    ]
    assert all(row.prompt_clarity_risk is not None for row in summary.recent_risk_sessions)
    assert all(row.project_complexity_signal is not None for row in summary.recent_risk_sessions)
    assert summary.failed_commands
    assert len(summary.failed_commands) == 1
    assert summary.failed_commands[0].failure_count == 5
    assert summary.failed_commands[0].session_count == 3
    assert summary.failed_commands[0].agents == ("claude", "codex", "pi")
    assert len(summary.repeated_files) == 1
    assert summary.repeated_files[0].activity_count == 5
    assert summary.repeated_files[0].session_count == 3
    assert summary.repeated_files[0].agents == ("claude", "codex", "pi")
    assert "Inspect the top failed commands" in " ".join(summary.recommendations)


def test_store_aggregate_summary_filters_by_agent_and_project(tmp_path) -> None:
    codex_path = FIXTURE_DIR / "basic-session.jsonl"
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = source_for_fixture(codex_path)
    bundle = CodexAdapter().parse_source(source)
    assert bundle.session is not None
    store.insert_parsed_bundle(source, bundle)
    add_summary_analysis_rows(store, bundle.session.session_id, "healthy", 0.1)

    codex_summary = store.aggregate_summary(SummaryFilters(agent_name="codex"))
    pi_summary = store.aggregate_summary(SummaryFilters(agent_name="pi"))
    project_summary = store.aggregate_summary(SummaryFilters(project_path="/tmp/session-doctor"))
    other_project_summary = store.aggregate_summary(SummaryFilters(project_path="/tmp/other"))

    assert codex_summary.total_sessions == 1
    assert pi_summary.total_sessions == 0
    assert project_summary.total_sessions == 1
    assert other_project_summary.total_sessions == 0


def test_store_repeated_files_groups_canonical_path_when_project_metadata_differs(
    tmp_path,
) -> None:
    canonical_path = "/tmp/project/src/app.py"
    sessions = (
        Session(
            session_id="session-with-project",
            source_id="source-with-project",
            agent_name=AgentName.CODEX,
            cwd="/tmp/project",
            project_path="/tmp/project",
        ),
        Session(
            session_id="session-without-project",
            source_id="source-without-project",
            agent_name=AgentName.PI,
        ),
    )
    activities = (
        FileActivity(
            file_activity_id="file-with-project",
            session_id=sessions[0].session_id,
            path="src/app.py",
            normalized_path="src/app.py",
            canonical_path=canonical_path,
            project_relative_path="src/app.py",
            path_resolution="cwd",
            operation="edit",
        ),
        FileActivity(
            file_activity_id="file-without-project",
            session_id=sessions[1].session_id,
            path=canonical_path,
            normalized_path=canonical_path,
            canonical_path=canonical_path,
            path_resolution="absolute",
            operation="edit",
        ),
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    for session, activity in zip(sessions, activities, strict=True):
        source = SessionSource(
            source_id=session.source_id,
            agent_name=session.agent_name,
            source_path=f"/tmp/{session.source_id}.jsonl",
        )
        store.insert_parsed_bundle(
            source,
            ParsedSessionBundle(session=session, file_activities=[activity]),
        )
        add_summary_analysis_rows(store, session.session_id, "tooling_blocked", 0.9)

    summary = store.aggregate_summary(SummaryFilters())

    assert len(summary.repeated_files) == 1
    assert summary.repeated_files[0].path == canonical_path
    assert summary.repeated_files[0].activity_count == 2
    assert summary.repeated_files[0].session_count == 2
    assert summary.repeated_files[0].agents == ("codex", "pi")


def test_store_aggregate_summary_redacts_commands_and_home_paths(tmp_path) -> None:
    home_file = Path.home() / "project" / "src" / "app.py"
    source = SessionSource(
        source_id="source-summary-redaction",
        agent_name=AgentName.CODEX,
        source_path="/tmp/source.jsonl",
    )
    session = Session(
        session_id="session-summary-redaction",
        source_id=source.source_id,
        agent_name=AgentName.CODEX,
        cwd=str(Path.home() / "project"),
        started_at=datetime(2026, 5, 6, 8, 0),
    )
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id="event-command",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=1,
            ),
            RawEvent(
                event_id="event-file-1",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=2,
            ),
            RawEvent(
                event_id="event-file-2",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=3,
            ),
        ],
        command_runs=[
            CommandRun(
                command_run_id="command-secret",
                session_id=session.session_id,
                source_event_id="event-command",
                command=f"TOKEN=supersecret cat {Path.home()}/project/.env",
                exit_code=1,
            )
        ],
        file_activities=[
            FileActivity(
                file_activity_id="file-1",
                session_id=session.session_id,
                source_event_id="event-file-1",
                path=str(home_file),
                operation="edit",
            ),
            FileActivity(
                file_activity_id="file-2",
                session_id=session.session_id,
                source_event_id="event-file-2",
                path=str(home_file),
                operation="edit",
            ),
        ],
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_parsed_bundle(source, bundle)
    add_summary_analysis_rows(store, session.session_id, "tooling_blocked", 0.9)

    summary = store.aggregate_summary(SummaryFilters())

    assert summary.failed_commands[0].command == "TOKEN=<redacted> cat ~/project/.env"
    assert "supersecret" not in summary.failed_commands[0].command
    assert str(Path.home()) not in summary.failed_commands[0].command
    assert summary.project_counts[0].project_path.startswith("~/")
    assert summary.repeated_files[0].path.startswith("~/")


def test_store_aggregate_summary_uses_structured_command_failure_metadata(tmp_path) -> None:
    source = SessionSource(
        source_id="source-structured-command-failure",
        agent_name=AgentName.CODEX,
        source_path="/tmp/structured-command-failure.jsonl",
    )
    session = Session(
        session_id="session-structured-command-failure",
        source_id=source.source_id,
        agent_name=source.agent_name,
    )
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    store.insert_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=session,
            command_runs=[
                CommandRun(
                    command_run_id="command-not-cancelled",
                    session_id=session.session_id,
                    command="echo safe",
                    exit_code=0,
                    metadata={"cancelled": False, "note": 'contains "interrupted": true text'},
                ),
                CommandRun(
                    command_run_id="command-interrupted",
                    session_id=session.session_id,
                    command="echo interrupted",
                    exit_code=0,
                    metadata={"interrupted": True},
                ),
            ],
        ),
    )
    with duckdb.connect(str(store.database_path)) as connection:
        connection.execute(
            "UPDATE command_runs SET command_display = ? WHERE command_run_id = ?",
            ["tool --api-key TOP_SECRET", "command-interrupted"],
        )

    summary = store.aggregate_summary(SummaryFilters())

    assert len(summary.failed_commands) == 1
    assert summary.failed_commands[0].command == "tool --api-key <redacted>"
    assert "TOP_SECRET" not in summary.failed_commands[0].command


def test_store_aggregate_summary_recommendations_use_uncapped_labels(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    sessions = [
        Session(
            session_id="session-abandoned",
            source_id="source-abandoned",
            agent_name=AgentName.CODEX,
            started_at=datetime(2026, 5, 6, 8, 0),
        ),
        Session(
            session_id="session-tooling",
            source_id="source-tooling",
            agent_name=AgentName.CODEX,
            started_at=datetime(2026, 5, 6, 9, 0),
        ),
    ]
    for session in sessions:
        source = SessionSource(
            source_id=session.source_id,
            agent_name=AgentName.CODEX,
            source_path=f"/tmp/{session.source_id}.jsonl",
        )
        store.insert_parsed_bundle(source, ParsedSessionBundle(session=session))

    add_summary_analysis_rows(store, "session-abandoned", "abandoned_or_stopped", 0.8)
    add_summary_analysis_rows(store, "session-tooling", "tooling_blocked", 0.8)

    summary = store.aggregate_summary(SummaryFilters(limit=1))

    assert [row.label for row in summary.classification_counts] == ["abandoned_or_stopped"]
    assert "Inspect the top failed commands for tooling blockers." in summary.recommendations


def test_store_aggregate_summary_empty_filter_recommendation_is_filter_specific(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = source_for_fixture(FIXTURE_DIR / "basic-session.jsonl")
    bundle = CodexAdapter().parse_source(source)
    store.insert_parsed_bundle(source, bundle)

    summary = store.aggregate_summary(SummaryFilters(agent_name="pi"))

    assert summary.total_sessions == 0
    assert summary.recommendations == (
        "No sessions match the current filters; adjust filters or ingest more sessions.",
    )


def source_for_fixture(path: Path) -> SessionSource:
    return SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, path),
        agent_name=AgentName.CODEX,
        source_path=str(path),
    )


def add_summary_analysis_rows(
    store: DuckDBStore,
    session_id: str,
    label: str,
    risk_score: float,
) -> None:
    analysis_run = AnalysisRun(
        analysis_run_id=f"analysis-{session_id}",
        session_id=session_id,
        analyzer_version="phase6",
        started_at=datetime(2026, 5, 8, 8, 0),
        completed_at=datetime(2026, 5, 8, 8, 1),
    )
    session_features = [
        SessionFeature(
            session_feature_id=f"feature-friction-{session_id}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            feature_name="friction_score",
            feature_value=f"{risk_score:.3f}",
            score=risk_score,
        ),
        SessionFeature(
            session_feature_id=f"feature-stuckness-{session_id}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            feature_name="stuckness_score",
            feature_value=f"{risk_score:.3f}",
            score=risk_score,
        ),
        SessionFeature(
            session_feature_id=f"feature-prompt-clarity-{session_id}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            feature_name="prompt_clarity_risk",
            feature_value=f"{risk_score:.3f}",
            score=risk_score,
        ),
        SessionFeature(
            session_feature_id=f"feature-agent-fit-{session_id}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            feature_name="agent_fit_risk",
            feature_value=f"{risk_score:.3f}",
            score=risk_score,
        ),
        SessionFeature(
            session_feature_id=f"feature-project-complexity-{session_id}",
            analysis_run_id=analysis_run.analysis_run_id,
            session_id=session_id,
            feature_name="project_complexity_signal",
            feature_value=f"{risk_score:.3f}",
            score=risk_score,
        ),
    ]
    classification = SessionClassification(
        session_classification_id=f"classification-{session_id}-{label}",
        analysis_run_id=analysis_run.analysis_run_id,
        session_id=session_id,
        label=label,
        score=risk_score,
        confidence=0.8,
        evidence_summary=f"Synthetic {label} evidence.",
    )
    store.replace_analysis_rows(analysis_run, [], session_features, [classification])
