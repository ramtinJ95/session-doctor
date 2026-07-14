from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.cli import app
from session_doctor.episode_workflow import analyze_session_episodes
from session_doctor.schemas import (
    AgentName,
    DelegationStatus,
    EpisodeMembershipStatus,
    Message,
    NormalizedRole,
    RawEvent,
    Session,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import DuckDBStore, SnapshotPruneBlocked

runner = CliRunner()


def persisted_conflicting_bundle(tmp_path: Path) -> tuple[DuckDBStore, str]:
    store = DuckDBStore(tmp_path / "episodes.duckdb")
    source = SessionSource(
        source_id="source-episodes",
        agent_name=AgentName.CODEX,
        source_path="/sessions/episodes.jsonl",
    )
    captured = store.capture_source(source, b"episode persistence fixture")
    captured_bundle = store.create_single_source_bundle(
        source,
        captured,
        "native-episodes",
    )
    store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=True)
    events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id=source.source_id,
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(5)
    ]
    session_id = "session-episodes"
    parsed = ParsedSessionBundle(
        session=Session(
            session_id=session_id,
            source_id=source.source_id,
            agent_name=AgentName.CODEX,
            native_session_id="native-episodes",
        ),
        raw_events=events,
        messages=[
            Message(
                message_id="message-first",
                session_id=session_id,
                source_event_id="event-0",
                role=NormalizedRole.USER,
                text="Inspect the parser",
            ),
            Message(
                message_id="message-final",
                session_id=session_id,
                source_event_id="event-2",
                role=NormalizedRole.ASSISTANT,
                text="Inspected.",
                metadata={"phase": "final_answer"},
            ),
            Message(
                message_id="message-second",
                session_id=session_id,
                source_event_id="event-3",
                role=NormalizedRole.USER,
                text="New task: update the schema",
            ),
        ],
        tool_calls=[
            ToolCall(
                tool_call_id="tool-call-first",
                session_id=session_id,
                source_event_id="event-1",
                native_tool_call_id="native-tool-first",
                name="shell",
            )
        ],
        tool_results=[
            ToolResult(
                tool_result_id="tool-result-conflicting",
                session_id=session_id,
                tool_call_id="tool-call-first",
                source_event_id="event-4",
                native_tool_call_id="native-tool-first",
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
    return store, session_id


def test_episode_analysis_persists_deterministic_round_trip_and_membership(tmp_path) -> None:
    store, session_id = persisted_conflicting_bundle(tmp_path)

    first = analyze_session_episodes(store, session_id, store.database_path)
    repeated = analyze_session_episodes(store, session_id, store.database_path)

    assert first == repeated
    assert first.schema_version == "episode-analysis-v2"
    assert len(first.episodes) == 2
    by_entity = {row.entity_id: row for row in first.entity_memberships}
    assert by_entity["event-0"].status is EpisodeMembershipStatus.ASSIGNED
    assert by_entity["tool-result-conflicting"].status is EpisodeMembershipStatus.AMBIGUOUS
    assert by_entity["session-episodes"].status is EpisodeMembershipStatus.UNASSIGNED
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        normalized_count = connection.execute(
            "SELECT count(*) FROM normalized_entities WHERE normalization_run_id = ?",
            [first.normalization_run_id],
        ).fetchone()
        membership_count = connection.execute(
            "SELECT count(*) FROM episode_entity_memberships WHERE analysis_identity = ?",
            [first.analysis_identity],
        ).fetchone()
        assert normalized_count == membership_count
        assert connection.execute("SELECT count(*) FROM episode_analysis_runs").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM episodes").fetchone() == (2,)


def test_force_prune_reports_and_removes_episode_projections(tmp_path) -> None:
    store, session_id = persisted_conflicting_bundle(tmp_path)
    analysis = analyze_session_episodes(store, session_id, store.database_path)
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        snapshot_row = connection.execute(
            """
            SELECT bundles.primary_snapshot_id
            FROM normalization_run_bundles AS links
            JOIN snapshot_bundles AS bundles USING (snapshot_bundle_id)
            WHERE links.normalization_run_id = ?
            """,
            [analysis.normalization_run_id],
        ).fetchone()
    assert snapshot_row is not None
    dependencies = store.snapshot_dependencies(str(snapshot_row[0]))
    assert dependencies.analysis_run_ids == (analysis.analysis_identity,)
    assert dependencies.derived_row_counts["episodes"] == 2
    assert dependencies.derived_row_counts["episode_entity_memberships"] == len(
        analysis.entity_memberships
    )

    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(str(snapshot_row[0]))
    store.prune_snapshot(str(snapshot_row[0]), force=True)

    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        for table_name in (
            "semantic_analysis_runs",
            "episode_analysis_runs",
            "episodes",
            "episode_boundaries",
            "episode_observations",
            "episode_entity_memberships",
            "episode_delegations",
            "episode_topology_projections",
            "episode_topology_projection_delegations",
            "episode_topology_projection_unavailable_children",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table_name}").fetchone() == (0,)


def test_claude_delegation_uses_spawn_provenance_without_synthetic_ordering(
    tmp_path,
) -> None:
    database = tmp_path / "delegation.duckdb"
    fixture = Path("tests/fixtures/claude/topology")
    ingested = runner.invoke(
        app,
        ["ingest", "--agent", "claude", "--source", str(fixture), "--db", str(database)],
    )
    assert ingested.exit_code == 0, ingested.stdout
    store = DuckDBStore(database)
    with duckdb.connect(str(database), read_only=True) as connection:
        root_row = connection.execute(
            "SELECT session_id FROM sessions WHERE NOT is_sidechain"
        ).fetchone()
        child_row = connection.execute(
            """
            SELECT session_id FROM sessions
            WHERE is_sidechain AND metadata_json LIKE '%agent-tool-2%'
            """
        ).fetchone()
    assert root_row is not None and child_row is not None

    root_analysis = analyze_session_episodes(store, str(root_row[0]), database)

    assert root_analysis.delegations == []
    assert root_analysis.topology_projection is not None
    assert len(root_analysis.topology_projection.delegations) == 1
    assert root_analysis.topology_projection.delegations[0].status is DelegationStatus.LINKED
    assert root_analysis.topology_projection.delegations[0].parent_analysis_identity == (
        root_analysis.analysis_identity
    )
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM episode_delegations").fetchone() == (2,)

    analysis = analyze_session_episodes(store, str(child_row[0]), database)

    assert len(analysis.episodes) == 1
    delegation = analysis.delegations[0]
    assert delegation.status is DelegationStatus.LINKED
    assert delegation.spawn_tool_call_id is not None
    assert delegation.spawn_event_id is not None
    assert delegation.parent_episode_id is not None
    assert delegation.rollup_owner_episode_id != analysis.episodes[0].episode_id
    assert analysis.episodes[0].aggregate_eligibility.value == "ineligible_delegated_child"
    assert analysis.topology_projection is not None
    assert delegation.delegation_id in {
        row.delegation_id for row in analysis.topology_projection.delegations
    }
    assert delegation.provenance["ordering"] == "source_local_only"
    assert all(
        not membership.additive_aggregate_eligible for membership in analysis.entity_memberships
    )
    with duckdb.connect(str(database), read_only=True) as connection:
        tables = {str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()}
        columns = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name IN ('episode_analysis_runs', 'episodes',
                    'episode_delegations', 'episode_entity_memberships')
                """
            ).fetchall()
        }
    assert not any("continuation" in value or "family" in value for value in tables | columns)


def test_message_id_fallback_anchor_receives_episode_membership(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "message-anchor.duckdb")
    source = SessionSource(
        source_id="message-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/message.jsonl",
    )
    captured = store.capture_source(source, b"message")
    captured_bundle = store.create_single_source_bundle(source, captured, source.source_id)
    store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=True)
    session_id = "message-session"
    parsed = ParsedSessionBundle(
        session=Session(
            session_id=session_id,
            source_id=source.source_id,
            agent_name=AgentName.CODEX,
        ),
        messages=[
            Message(
                message_id="fallback-message-anchor",
                session_id=session_id,
                role=NormalizedRole.USER,
                text="Inspect this exact input",
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

    analysis = analyze_session_episodes(store, session_id, store.database_path)

    membership = next(
        row for row in analysis.entity_memberships if row.entity_id == "fallback-message-anchor"
    )
    assert membership.status is EpisodeMembershipStatus.ASSIGNED
    assert membership.evidence_anchor_ids == ["fallback-message-anchor"]


def test_parent_topology_preserves_unavailable_child_without_failing(tmp_path) -> None:
    store, parent_session_id = persisted_conflicting_bundle(tmp_path)
    child_source = SessionSource(
        source_id="unavailable-child-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/unavailable-child.jsonl",
    )
    captured = store.capture_source(child_source, b"captured-child")
    child_bundle = store.create_single_source_bundle(
        child_source,
        captured,
        "unavailable-child-native",
    )
    store.record_lifecycle(child_bundle.snapshot_bundle_id, terminal_observed=False)
    child_session_id = "unavailable-child-session"
    parsed = ParsedSessionBundle(
        session=Session(
            session_id=child_session_id,
            source_id=child_source.source_id,
            agent_name=AgentName.CODEX,
            native_session_id="unavailable-child-native",
            parent_session_id=parent_session_id,
            is_sidechain=True,
            metadata={
                "parent_link_status": "linked",
                "subagent_metadata": {"tool_use_id": "native-tool-first"},
            },
        ),
        raw_events=[
            RawEvent(
                event_id="unavailable-child-event",
                source_id=child_source.source_id,
                agent_name=AgentName.CODEX,
                record_index=0,
            )
        ],
    )
    store.insert_parsed_bundle(
        child_source,
        parsed,
        captured,
        child_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    store.capture_source(child_source, b"newer-unparsed-child")

    parent = analyze_session_episodes(store, parent_session_id, store.database_path)

    assert parent.topology_projection is not None
    assert parent.topology_projection.delegations == []
    assert [
        (row.child_session_id, row.reason)
        for row in parent.topology_projection.unavailable_children
    ] == [
        (
            child_session_id,
            "analysis_unavailable:latest capture has no current normalized projection",
        )
    ]
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM episode_topology_projections "
            "WHERE topology_projection_id = ?",
            [parent.topology_projection.topology_projection_id],
        ).fetchone()
    assert payload is not None
    assert child_session_id in str(payload[0])
    dependencies = store.snapshot_dependencies(captured.snapshot_id)
    assert dependencies.derived_row_counts["episode_topology_projections"] == 1
    assert dependencies.derived_row_counts["episode_topology_projection_unavailable_children"] == 1

    store.prune_snapshot(captured.snapshot_id, force=True)

    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM episode_topology_projections"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM episode_topology_projection_unavailable_children"
        ).fetchone() == (0,)


def test_parent_topology_records_child_with_no_episode(tmp_path) -> None:
    store, parent_session_id = persisted_conflicting_bundle(tmp_path)
    child_source = SessionSource(
        source_id="empty-child-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/empty-child.jsonl",
    )
    captured = store.capture_source(child_source, b"empty-child")
    child_bundle = store.create_single_source_bundle(
        child_source,
        captured,
        "empty-child-native",
    )
    store.record_lifecycle(child_bundle.snapshot_bundle_id, terminal_observed=True)
    child_session_id = "empty-child-session"
    store.insert_parsed_bundle(
        child_source,
        ParsedSessionBundle(
            session=Session(
                session_id=child_session_id,
                source_id=child_source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="empty-child-native",
                parent_session_id=parent_session_id,
                is_sidechain=True,
                metadata={"parent_link_status": "missing"},
            )
        ),
        captured,
        child_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )

    parent = analyze_session_episodes(store, parent_session_id, store.database_path)

    assert parent.topology_projection is not None
    assert [
        (row.child_session_id, row.reason)
        for row in parent.topology_projection.unavailable_children
    ] == [(child_session_id, "child_has_no_episode_delegation")]


def test_pruning_unnormalized_predecessor_removes_downstream_episode_analysis(
    tmp_path,
) -> None:
    store = DuckDBStore(tmp_path / "settled-prune.duckdb")
    source = SessionSource(
        source_id="settled-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/settled.jsonl",
    )
    first_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
    first = store.capture_source(source, b"same", captured_at=first_at)
    first_bundle = store.create_single_source_bundle(source, first, source.source_id)
    store.record_lifecycle(first_bundle.snapshot_bundle_id, terminal_observed=False)
    second = store.capture_source(
        source,
        b"same",
        captured_at=first_at + timedelta(seconds=31),
    )
    second_bundle = store.create_single_source_bundle(source, second, source.source_id)
    store.record_lifecycle(second_bundle.snapshot_bundle_id, terminal_observed=False)
    session_id = "settled-session"
    parsed = ParsedSessionBundle(
        session=Session(
            session_id=session_id,
            source_id=source.source_id,
            agent_name=AgentName.CODEX,
        ),
        raw_events=[
            RawEvent(
                event_id="settled-event",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=0,
            )
        ],
        messages=[
            Message(
                message_id="settled-message",
                session_id=session_id,
                source_event_id="settled-event",
                role=NormalizedRole.USER,
                text="Inspect settlement",
            )
        ],
    )
    store.insert_parsed_bundle(
        source,
        parsed,
        second,
        second_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    analysis = analyze_session_episodes(store, session_id, store.database_path)

    dependencies = store.snapshot_dependencies(first.snapshot_id)

    assert dependencies.analysis_run_ids == (analysis.analysis_identity,)
    store.prune_snapshot(first.snapshot_id, force=True)
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM episode_analysis_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM episodes").fetchone() == (0,)


def test_delegated_episode_without_parent_evidence_is_explicitly_unavailable(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "unavailable.duckdb")
    source = SessionSource(
        source_id="child-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/child.jsonl",
    )
    captured = store.capture_source(source, b"child")
    captured_bundle = store.create_single_source_bundle(source, captured, source.source_id)
    store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=False)
    session_id = "child-session"
    parsed = ParsedSessionBundle(
        session=Session(
            session_id=session_id,
            source_id=source.source_id,
            agent_name=AgentName.CODEX,
            is_sidechain=True,
            metadata={"parent_link_status": "missing"},
        ),
        raw_events=[
            RawEvent(
                event_id="child-event",
                source_id=source.source_id,
                agent_name=AgentName.CODEX,
                record_index=0,
                timestamp=datetime(2026, 7, 14, tzinfo=UTC),
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

    analysis = analyze_session_episodes(store, session_id, store.database_path)

    assert analysis.delegations[0].status is DelegationStatus.UNAVAILABLE
    assert analysis.delegations[0].parent_episode_id is None
    assert analysis.episodes[0].rollup_owner_episode_id == analysis.episodes[0].episode_id
    assert analysis.episodes[0].aggregate_eligibility.value == "ineligible_delegated_child"
