from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.cli import UNAVAILABLE_REBUILD_MESSAGE, app
from session_doctor.episode_workflow import EpisodeAnalysisUnavailable, analyze_session_episodes
from session_doctor.schemas import (
    AgentName,
    BoundaryDecision,
    Message,
    NormalizedRole,
    RawEvent,
    Session,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.segmentation import SEGMENTATION_VERSION, broad_goal_similarity, segment_session
from session_doctor.store import DuckDBStore
from session_doctor.store.lifecycle import LifecycleObservation

runner = CliRunner()


def lifecycle(state: str = "terminal_observed") -> LifecycleObservation:
    return LifecycleObservation(
        lifecycle_observation_id=f"lifecycle-{state}",
        snapshot_bundle_id="bundle-1",
        state=state,
        observed_at=datetime(2026, 7, 13, tzinfo=UTC),
        evidence={"reason": "test"},
    )


def bundle(*user_texts: str, closed_after: set[int] | None = None, session_id: str = "s1"):
    closed_after = closed_after or set()
    messages = []
    for index, text in enumerate(user_texts):
        messages.append(
            Message(
                message_id=f"u-{index}",
                session_id=session_id,
                source_event_id=f"event-u-{index}",
                role=NormalizedRole.USER,
                text=text,
                timestamp=datetime(2026, 7, 13, tzinfo=UTC) + timedelta(days=index * 10),
            )
        )
        if index < len(user_texts) - 1:
            messages.append(
                Message(
                    message_id=f"a-{index}",
                    session_id=session_id,
                    source_event_id=f"event-a-{index}",
                    role=NormalizedRole.ASSISTANT,
                    text="response",
                    metadata={"phase": "final_answer"} if index in closed_after else {},
                )
            )
    return ParsedSessionBundle(
        session=Session(
            session_id=session_id,
            source_id=f"source-{session_id}",
            agent_name=AgentName.CODEX,
        ),
        messages=messages,
    )


def test_explicit_new_task_splits_without_closure() -> None:
    result = segment_session(
        bundle("Fix parser tests", "New task: update the release notes"), lifecycle()
    )
    assert len(result.episodes) == 2
    assert result.boundaries[0].decision is BoundaryDecision.SPLIT
    assert result.observations[0].observation_kind == (
        "interrupted_unknown_by_explicit_replacement"
    )


def test_separate_question_is_an_explicit_split_marker() -> None:
    result = segment_session(
        bundle("Fix parser tests", "Separate question: how is this packaged?"), lifecycle()
    )
    assert result.boundaries[0].decision is BoundaryDecision.SPLIT


def test_explicit_new_task_after_closure_is_not_interrupted_unknown() -> None:
    result = segment_session(
        bundle(
            "Fix parser tests",
            "New task: update release notes",
            closed_after={0},
        ),
        lifecycle(),
    )
    assert result.boundaries[0].decision is BoundaryDecision.SPLIT
    assert not result.observations


@pytest.mark.parametrize(
    "follow_up",
    [
        "Actually, fix the parser test first",
        "Please continue with the parser test",
        "Review that parser test change",
        "No, fix the parser test first",
        "Fix parser tests",
    ],
)
def test_correction_review_and_repeat_remain_one_episode(follow_up: str) -> None:
    result = segment_session(bundle("Fix parser tests", follow_up), lifecycle())
    assert len(result.episodes) == 1
    assert result.boundaries[0].decision is BoundaryDecision.NO_SPLIT


@pytest.mark.parametrize(
    "follow_up",
    [
        "Resume the work.",
        "Check the result.",
        "Review the latest output.",
        "Validate the change.",
        "Confirm the final validation.",
        "Address the remaining work.",
        "Record the outcome.",
    ],
)
def test_calibrated_anaphoric_follow_ups_remain_one_episode(follow_up: str) -> None:
    result = segment_session(bundle("Inspect the implementation.", follow_up), lifecycle())
    assert result.boundaries[0].decision is BoundaryDecision.NO_SPLIT
    assert result.segmentation_version == "segmentation-v2" == SEGMENTATION_VERSION


def test_calibration_does_not_turn_generic_reporting_into_a_new_task() -> None:
    result = segment_session(
        bundle("Record the outcome.", "Report the remaining work."), lifecycle()
    )
    assert result.boundaries[0].decision is BoundaryDecision.AMBIGUOUS


def test_weak_topic_shift_merges_with_ambiguity() -> None:
    result = segment_session(
        bundle("Inspect parser behavior", "Check schema behavior"), lifecycle()
    )
    assert len(result.episodes) == 1
    assert result.boundaries[0].decision is BoundaryDecision.AMBIGUOUS
    assert result.observations[0].observation_kind == "ambiguous_boundary_merged"


def test_elapsed_time_alone_does_not_split() -> None:
    result = segment_session(bundle("Fix parser tests", "Fix parser tests"), lifecycle())
    assert len(result.episodes) == 1
    assert result.boundaries[0].decision is BoundaryDecision.NO_SPLIT


def test_closure_and_strong_topic_shift_split() -> None:
    result = segment_session(
        bundle("Repair parser regression", "Draft launch announcement", closed_after={0}),
        lifecycle(),
    )
    assert len(result.episodes) == 2
    assert result.boundaries[0].decision is BoundaryDecision.SPLIT
    assert "event-a-0" in result.boundaries[0].evidence_anchor_ids
    assert "event-a-0" in result.episodes[0].event_anchor_ids


def test_pending_tool_work_prevents_closure_split() -> None:
    pending = bundle(
        "Repair parser regression",
        "Draft launch announcement",
        closed_after={0},
    )
    pending.raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-s1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(5)
    ]
    pending.messages[0].source_event_id = "event-0"
    pending.messages[1].source_event_id = "event-2"
    pending.messages[2].source_event_id = "event-3"
    pending.tool_calls = [
        ToolCall(
            tool_call_id="pending-call",
            session_id="s1",
            source_event_id="event-1",
            name="shell",
        )
    ]
    pending.tool_results = [
        ToolResult(
            tool_result_id="late-result",
            session_id="s1",
            tool_call_id="pending-call",
            source_event_id="event-4",
        )
    ]
    result = segment_session(pending, lifecycle())
    assert result.boundaries[0].decision is BoundaryDecision.AMBIGUOUS


def test_sessions_never_merge_and_active_lifecycle_is_provisional() -> None:
    first = segment_session(bundle("Continue parser work", session_id="s1"), lifecycle())
    second = segment_session(
        bundle("Continue parser work", session_id="s2"), lifecycle("possibly_active")
    )
    assert first.episodes[0].session_id == "s1"
    assert second.episodes[0].session_id == "s2"
    assert second.episodes[0].provisional


def test_broad_goal_similarity_is_unicode_aware() -> None:
    assert broad_goal_similarity("Ｆｉｘ Café parser", "fix café parser") == 1.0
    assert broad_goal_similarity("✨", "✨") is None


@pytest.mark.parametrize(
    ("arguments", "command"),
    [
        (["summary", "--db", "ignored.duckdb"], "summary"),
        (["summary", "--help"], "summary"),
        (["trends", "--format", "html"], "trends"),
        (["report", "session-1", "--db", "ignored.duckdb"], "report"),
        (["graph", "session-1"], "graph"),
        (["projects", "list", "--agent", "codex"], "projects list"),
    ],
)
def test_downstream_commands_are_explicitly_unavailable(arguments, command) -> None:
    result = runner.invoke(app, arguments)
    assert result.exit_code == 1
    assert UNAVAILABLE_REBUILD_MESSAGE.format(command=command) in result.stdout


def test_episode_analysis_json_contains_no_v1_scores(tmp_path) -> None:
    fixture = "tests/fixtures/codex/basic-session.jsonl"
    database = tmp_path / "session-doctor.duckdb"
    ingested = runner.invoke(
        app,
        ["ingest", "--agent", "codex", "--source", fixture, "--db", str(database)],
    )
    assert ingested.exit_code == 0
    with duckdb.connect(str(database), read_only=True) as connection:
        session_row = connection.execute("SELECT session_id FROM sessions").fetchone()
        assert session_row is not None
        session_id = str(session_row[0])
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    analyzed = runner.invoke(
        app,
        ["analyze", session_id, "--db", str(database), "--format", "json"],
    )
    assert analyzed.exit_code == 0
    payload = json.loads(analyzed.stdout)
    assert payload["episodes"]
    assert "analysis_runs" not in tables
    assert "session_classifications" not in tables
    assert "score" not in analyzed.stdout


def test_schema_v11_rebuild_drops_legacy_analysis_tables(tmp_path) -> None:
    database = tmp_path / "legacy-v9.duckdb"
    store = DuckDBStore(database)
    source = SessionSource(
        source_id="migration-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/migration.jsonl",
    )
    captured = store.capture_source(source, b"durable migration bytes")
    bundle_row = store.create_single_source_bundle(source, captured, "migration-native")
    lifecycle_row = store.record_lifecycle(bundle_row.snapshot_bundle_id, terminal_observed=True)
    with duckdb.connect(str(database)) as connection:
        for table in (
            "analysis_runs",
            "message_features",
            "session_features",
            "session_classifications",
        ):
            connection.execute(f"CREATE TABLE {table} (id VARCHAR)")
        connection.execute("DELETE FROM schema_migrations")
        connection.execute("INSERT INTO schema_migrations (version) VALUES (9)")
    store.initialize()
    with duckdb.connect(str(database), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert (
        not {
            "analysis_runs",
            "message_features",
            "session_features",
            "session_classifications",
        }
        & tables
    )
    assert store.load_snapshot_bytes(captured.snapshot_id) == b"durable migration bytes"
    migrated_lifecycle = store.lifecycle_for_bundle(bundle_row.snapshot_bundle_id)
    assert migrated_lifecycle is not None
    assert migrated_lifecycle.lifecycle_observation_id == lifecycle_row.lifecycle_observation_id


def test_latest_capture_bundle_is_used_for_a_b_a_history(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "history.duckdb")
    source = SessionSource(
        source_id="source-history",
        agent_name=AgentName.CODEX,
        source_path="/sessions/history.jsonl",
    )
    latest_bundle_id = ""
    for content, event_id in ((b"A", "event-a"), (b"B", "event-b"), (b"A", "event-a")):
        captured = store.capture_source(source, content)
        captured_bundle = store.create_single_source_bundle(source, captured, "native-history")
        latest_bundle_id = captured_bundle.snapshot_bundle_id
        parsed = ParsedSessionBundle(
            session=Session(
                session_id="session-history",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="native-history",
            ),
            raw_events=[
                RawEvent(
                    event_id=event_id,
                    source_id=source.source_id,
                    agent_name=AgentName.CODEX,
                    record_index=0,
                )
            ],
            messages=[
                Message(
                    message_id=f"message-{event_id}",
                    session_id="session-history",
                    source_event_id=event_id,
                    role=NormalizedRole.USER,
                    text=content.decode(),
                )
            ],
        )
        store.insert_parsed_bundle(
            source,
            parsed,
            captured,
            captured_bundle,
            adapter_version=CodexAdapter.version,
            capability_declarations=CodexAdapter.capabilities,
        )
        store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=False)
    analysis = analyze_session_episodes(store, "session-history", store.database_path)
    latest_lifecycle = store.lifecycle_for_bundle(latest_bundle_id)
    assert latest_lifecycle is not None
    assert analysis.episodes[0].first_user_anchor_id == "event-a"
    assert analysis.lifecycle_observation_id == latest_lifecycle.lifecycle_observation_id
    store.capture_source(source, b"unparsed-C")
    with pytest.raises(EpisodeAnalysisUnavailable, match="latest capture"):
        analyze_session_episodes(store, "session-history", store.database_path)


def test_v1_payload_and_producer_modules_are_absent() -> None:
    from pathlib import Path

    source_root = Path("src/session_doctor")
    forbidden_modules = (
        "analysis_workflow.py",
        "report_models.py",
        "report_payload.py",
        "graph_projection.py",
        "summary_payload.py",
        "trend_payload.py",
    )
    assert all(not (source_root / name).exists() for name in forbidden_modules)
    forbidden_terms = ("friction_score", "stuckness_score", "user_stuck")
    for path in source_root.rglob("*.py"):
        if path.name == "migrations.py":
            continue
        text = path.read_text()
        assert not any(term in text for term in forbidden_terms), path
