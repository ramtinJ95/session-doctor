from __future__ import annotations

from datetime import datetime

import duckdb

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis.version import ANALYZER_VERSION
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    Message,
    MessageFeature,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    SessionClassification,
    SessionFeature,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import AnalysisCompatibility, DuckDBStore
from session_doctor.store.connection import read_connection
from session_doctor.store.pattern_readers import latest_problematic_session_ids


def test_diagnostic_snapshot_loads_exact_session_topology_and_indexes(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "diagnostic.duckdb")
    insert_session(store, "root", "root-source")
    selected = Session(
        session_id="selected",
        source_id="selected-source",
        agent_name=AgentName.CLAUDE,
        parent_session_id="root",
        is_sidechain=True,
    )
    source = SessionSource(
        source_id=selected.source_id,
        agent_name=selected.agent_name,
        source_path="/private/selected.jsonl",
    )
    store.insert_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=selected,
            raw_events=[
                RawEvent(
                    event_id="event-1",
                    source_id=selected.source_id,
                    agent_name=selected.agent_name,
                    record_index=1,
                )
            ],
            messages=[
                Message(
                    message_id="message-1",
                    session_id=selected.session_id,
                    role=NormalizedRole.USER,
                    source_event_id="missing-event",
                    parent_message_id="missing-message",
                    text="private text",
                    text_length=12,
                )
            ],
            tool_calls=[
                ToolCall(
                    tool_call_id="call-1",
                    session_id=selected.session_id,
                    source_event_id="event-1",
                    name="shell",
                )
            ],
            tool_results=[
                ToolResult(
                    tool_result_id="result-1",
                    session_id=selected.session_id,
                    source_event_id="event-1",
                    tool_call_id="missing-call",
                )
            ],
            parse_warnings=[
                ParseWarning(
                    warning_id="warning-1",
                    source_id=selected.source_id,
                    record_index=99,
                    message="private warning",
                )
            ],
        ),
    )
    insert_session(store, "child-b", "child-b-source", parent_session_id="selected")
    insert_session(store, "child-a", "child-a-source", parent_session_id="selected")

    snapshot = store.load_diagnostic_snapshot("selected")

    assert snapshot is not None
    assert snapshot.normalized.session.session_id == "selected"
    assert [row.session_id for row in snapshot.topology_references] == [
        "root",
        "child-a",
        "child-b",
    ]
    assert [row.relationship for row in snapshot.topology_references] == [
        "parent",
        "child",
        "child",
    ]
    assert tuple(snapshot.indexes.raw_events_by_id) == ("event-1",)
    assert snapshot.unresolved.message_source_event_ids == ("missing-event",)
    assert snapshot.unresolved.message_parent_ids == ("missing-message",)
    assert snapshot.unresolved.tool_result_tool_call_ids == ("missing-call",)
    assert snapshot.unresolved.warning_ids == ("warning-1",)
    assert snapshot.analysis.compatibility is AnalysisCompatibility.MISSING
    assert snapshot.analysis.action == "session-doctor analyze selected"


def test_diagnostic_snapshot_loads_only_latest_current_analysis_run(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "diagnostic.duckdb")
    session = insert_session(store, "selected", "selected-source")
    current_run = AnalysisRun(
        analysis_run_id="current-run",
        session_id=session.session_id,
        analyzer_version=ANALYZER_VERSION,
        completed_at=datetime(2026, 7, 2),
    )
    current_message_feature = MessageFeature(
        message_feature_id="current-message-feature",
        analysis_run_id=current_run.analysis_run_id,
        session_id=session.session_id,
        message_id="missing-message",
        source_event_id="missing-event",
        feature_name="correction_marker",
        feature_value="true",
    )
    current_session_feature = SessionFeature(
        session_feature_id="current-session-feature",
        analysis_run_id=current_run.analysis_run_id,
        session_id=session.session_id,
        feature_name="friction_score",
        feature_value="0.5",
        score=0.5,
    )
    current_classification = SessionClassification(
        session_classification_id="current-classification",
        analysis_run_id=current_run.analysis_run_id,
        session_id=session.session_id,
        label="tooling_blocked",
        score=0.6,
        confidence=0.7,
        evidence_event_ids=["missing-event"],
        evidence_summary="Observed failure evidence.",
    )
    store.replace_analysis_rows(
        current_run,
        [current_message_feature],
        [current_session_feature],
        [current_classification],
    )
    with duckdb.connect(str(store.database_path)) as connection:
        connection.execute(
            """
            INSERT INTO analysis_runs
            (analysis_run_id, session_id, completed_at, analyzer_version)
            VALUES ('old-run', 'selected', TIMESTAMP '2026-07-01', 'phase5')
            """
        )
        connection.execute(
            """
            INSERT INTO session_features
            (session_feature_id, analysis_run_id, session_id, feature_name, feature_value, score)
            VALUES ('old-feature', 'old-run', 'selected', 'friction_score', '1', 1)
            """
        )

    snapshot = store.load_diagnostic_snapshot("selected")

    assert snapshot is not None
    assert snapshot.analysis.compatibility is AnalysisCompatibility.CURRENT
    assert snapshot.analysis.analysis_run_id == "current-run"
    assert [row.session_feature_id for row in snapshot.analysis.session_features] == [
        "current-session-feature"
    ]
    assert snapshot.unresolved.message_feature_message_ids == ("missing-message",)
    assert snapshot.unresolved.message_feature_source_event_ids == ("missing-event",)
    assert snapshot.unresolved.classification_source_event_ids == ("missing-event",)


def test_diagnostic_snapshot_excludes_stale_analysis_rows(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "diagnostic.duckdb")
    session = insert_session(store, "selected", "selected-source")
    stale_run = AnalysisRun(
        analysis_run_id="stale-run",
        session_id=session.session_id,
        analyzer_version="phase5",
    )
    stale_feature = SessionFeature(
        session_feature_id="stale-feature",
        analysis_run_id=stale_run.analysis_run_id,
        session_id=session.session_id,
        feature_name="friction_score",
        feature_value="1",
        score=1,
    )
    store.replace_analysis_rows(stale_run, [], [stale_feature], [])

    snapshot = store.load_diagnostic_snapshot("selected")

    assert snapshot is not None
    assert snapshot.analysis.compatibility is AnalysisCompatibility.STALE
    assert snapshot.analysis.observed_analyzer_version == "phase5"
    assert snapshot.analysis.session_features == ()
    assert snapshot.analysis.message_features == ()
    assert snapshot.analysis.classifications == ()


def test_problematic_file_eligibility_rejects_stale_analysis(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "diagnostic.duckdb")
    session = insert_session(store, "selected", "selected-source")
    stale_run = AnalysisRun(
        analysis_run_id="stale-run",
        session_id=session.session_id,
        analyzer_version="phase5",
    )
    stale_score = SessionFeature(
        session_feature_id="stale-score",
        analysis_run_id=stale_run.analysis_run_id,
        session_id=session.session_id,
        feature_name="friction_score",
        feature_value="1",
        score=1,
    )
    store.replace_analysis_rows(stale_run, [], [stale_score], [])

    with read_connection(store.database_path) as connection:
        eligible = latest_problematic_session_ids(connection)

    assert eligible == set()


def insert_session(
    store: DuckDBStore,
    session_id: str,
    source_id: str,
    *,
    parent_session_id: str | None = None,
) -> Session:
    session = Session(
        session_id=session_id,
        source_id=source_id,
        agent_name=AgentName.CLAUDE,
        parent_session_id=parent_session_id,
        is_sidechain=parent_session_id is not None,
    )
    store.insert_parsed_bundle(
        SessionSource(
            source_id=source_id,
            agent_name=session.agent_name,
            source_path=f"/private/{source_id}.jsonl",
        ),
        ParsedSessionBundle(session=session),
    )
    return session
