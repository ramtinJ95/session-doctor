from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.cli import UNAVAILABLE_REBUILD_MESSAGE, app
from session_doctor.schemas import (
    AgentName,
    BoundaryDecision,
    Message,
    NormalizedRole,
    Session,
)
from session_doctor.segmentation import broad_goal_similarity, segment_session
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


@pytest.mark.parametrize(
    "follow_up",
    [
        "Actually, fix the parser test first",
        "Please continue with the parser test",
        "Review that parser test change",
        "Fix parser tests",
    ],
)
def test_correction_review_and_repeat_remain_one_episode(follow_up: str) -> None:
    result = segment_session(bundle("Fix parser tests", follow_up), lifecycle())
    assert len(result.episodes) == 1
    assert result.boundaries[0].decision is BoundaryDecision.NO_SPLIT


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


@pytest.mark.parametrize(
    ("arguments", "command"),
    [
        (["summary"], "summary"),
        (["trends"], "trends"),
        (["report"], "report"),
        (["graph"], "graph"),
        (["projects", "list"], "projects list"),
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


def test_schema_v10_rebuild_drops_legacy_analysis_tables(tmp_path) -> None:
    database = tmp_path / "legacy-v9.duckdb"
    DuckDBStore(database).initialize()
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
    DuckDBStore(database).initialize()
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
