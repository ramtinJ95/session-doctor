from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.cli import app
from session_doctor.episode_workflow import (
    AnalysisInput,
    _child_capture_reference,
    _materialize_source_episodes,
    _warning_anchor,
    analyze_session_episodes,
)
from session_doctor.schemas import (
    AgentName,
    DelegationStatus,
    EpisodeAnalysis,
    EpisodeDelegation,
    EpisodeMembershipStatus,
    EpisodeTopologyProjection,
    Message,
    NormalizedRole,
    RawEvent,
    Session,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import DuckDBStore, SnapshotPruneBlocked
from session_doctor.store.lifecycle import LifecycleObservation
from session_doctor.store.normalization_runs import NormalizationRun, StoredNormalization

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


def persisted_parent_cycle(database: Path) -> DuckDBStore:
    store = DuckDBStore(database)
    captured_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
    for session_id, parent_session_id in (("cycle-a", "cycle-b"), ("cycle-b", "cycle-a")):
        source = SessionSource(
            source_id=f"{session_id}-source",
            agent_name=AgentName.CODEX,
            source_path=f"/sessions/{session_id}.jsonl",
        )
        captured = store.capture_source(
            source,
            session_id.encode(),
            captured_at=captured_at,
        )
        captured_bundle = store.create_single_source_bundle(source, captured, session_id)
        store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=True)
        store.insert_parsed_bundle(
            source,
            ParsedSessionBundle(
                session=Session(
                    session_id=session_id,
                    source_id=source.source_id,
                    agent_name=AgentName.CODEX,
                    native_session_id=session_id,
                    parent_session_id=parent_session_id,
                    is_sidechain=True,
                    metadata={
                        "parent_link_status": "linked",
                        "subagent_metadata": {"tool_use_id": f"spawn-{session_id}"},
                    },
                ),
                raw_events=[
                    RawEvent(
                        event_id=f"{session_id}-event",
                        source_id=source.source_id,
                        agent_name=AgentName.CODEX,
                        record_index=0,
                    )
                ],
            ),
            captured,
            captured_bundle,
            adapter_version=CodexAdapter.version,
            capability_declarations=CodexAdapter.capabilities,
        )
    return store


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


def test_parent_projection_uses_only_child_analysis_selected_for_current_capture(tmp_path) -> None:
    fixture = tmp_path / "topology"
    shutil.copytree(Path("tests/fixtures/claude/topology"), fixture)
    database = tmp_path / "recaptured-delegation.duckdb"
    ingest_args = [
        "ingest",
        "--agent",
        "claude",
        "--source",
        str(fixture),
        "--db",
        str(database),
    ]
    assert runner.invoke(app, ingest_args).exit_code == 0
    store = DuckDBStore(database)
    with duckdb.connect(str(database), read_only=True) as connection:
        root_row = connection.execute(
            "SELECT session_id FROM sessions WHERE NOT is_sidechain"
        ).fetchone()
    assert root_row is not None
    root_session_id = str(root_row[0])

    first = analyze_session_episodes(store, root_session_id, database)
    assert first.topology_projection is not None
    first_child_analysis_id = first.topology_projection.delegations[0].child_analysis_identity
    child_path = fixture / "project/session-root/subagents/agent-a.jsonl"
    appended = {
        "type": "assistant",
        "sessionId": "session-root",
        "uuid": "subagent-recaptured",
        "timestamp": "2026-01-01T00:00:05Z",
        "cwd": "/tmp/session-doctor",
        "isSidechain": True,
        "agentId": "agent-a",
        "message": {
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": "Recaptured result."}],
            "stop_reason": "end_turn",
        },
    }
    child_path.write_text(f"{child_path.read_text().rstrip()}\n{json.dumps(appended)}\n")
    assert runner.invoke(app, ingest_args).exit_code == 0

    current = analyze_session_episodes(store, root_session_id, database)

    assert current.topology_projection is not None
    assert len(current.topology_projection.delegations) == 1
    current_delegation = current.topology_projection.delegations[0]
    assert current_delegation.child_analysis_identity != first_child_analysis_id
    with duckdb.connect(str(database), read_only=True) as connection:
        delegation_count = connection.execute("SELECT count(*) FROM episode_delegations").fetchone()
    assert delegation_count is not None
    assert delegation_count[0] > len(current.topology_projection.delegations)


def test_child_first_analysis_finalizes_parent_after_recursion_deferral(tmp_path) -> None:
    database = tmp_path / "child-first.duckdb"
    ingested = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            "tests/fixtures/claude/topology",
            "--db",
            str(database),
        ],
    )
    assert ingested.exit_code == 0, ingested.stdout
    store = DuckDBStore(database)
    with duckdb.connect(str(database), read_only=True) as connection:
        child_row = connection.execute(
            "SELECT session_id FROM sessions "
            "WHERE is_sidechain AND metadata_json LIKE '%agent-tool-1%'"
        ).fetchone()
    assert child_row is not None
    child_session_id = str(child_row[0])

    child = analyze_session_episodes(store, child_session_id, database)

    parent_analysis_id = child.delegations[0].parent_analysis_identity
    assert parent_analysis_id is not None
    with duckdb.connect(str(database), read_only=True) as connection:
        projection_rows = connection.execute(
            "SELECT payload_json FROM episode_topology_projections WHERE analysis_identity = ?",
            [parent_analysis_id],
        ).fetchall()
    assert len(projection_rows) == 1
    parent_projection = EpisodeTopologyProjection.model_validate_json(str(projection_rows[0][0]))
    assert parent_projection.unavailable_children == []
    assert [row.child_analysis_identity for row in parent_projection.delegations] == [
        child.analysis_identity
    ]


def test_native_parent_cycle_is_deterministic_from_either_entry_session(tmp_path) -> None:
    first_store = persisted_parent_cycle(tmp_path / "cycle-first.duckdb")
    second_store = persisted_parent_cycle(tmp_path / "cycle-second.duckdb")

    analyze_session_episodes(first_store, "cycle-a", first_store.database_path)
    analyze_session_episodes(second_store, "cycle-b", second_store.database_path)

    def persisted_cycle_rows(database: Path) -> dict[str, tuple[str, str]]:
        with duckdb.connect(str(database), read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT run.session_id, run.analysis_identity, delegation.payload_json
                FROM episode_analysis_runs AS run
                JOIN episode_delegations AS delegation
                  ON delegation.child_analysis_identity = run.analysis_identity
                ORDER BY run.session_id
                """
            ).fetchall()
        return {
            str(session_id): (
                str(analysis_identity),
                str(EpisodeDelegation.model_validate_json(str(payload)).provenance["reason"]),
            )
            for session_id, analysis_identity, payload in rows
        }

    first_rows = persisted_cycle_rows(first_store.database_path)
    second_rows = persisted_cycle_rows(second_store.database_path)
    assert first_rows == second_rows
    assert {reason for _, reason in first_rows.values()} == {"native_parent_cycle"}


def test_child_capture_reference_uses_session_source_in_multi_source_bundle(tmp_path) -> None:
    database = tmp_path / "child-provenance.duckdb"
    ingested = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            "tests/fixtures/claude/topology",
            "--db",
            str(database),
        ],
    )
    assert ingested.exit_code == 0, ingested.stdout
    store = DuckDBStore(database)
    with duckdb.connect(str(database), read_only=True) as connection:
        child_row = connection.execute(
            """
            SELECT child.session_id, source.source_id, member.snapshot_id,
                member.logical_source_id, bundle.primary_snapshot_id,
                member.snapshot_bundle_id
            FROM sessions AS child
            JOIN session_sources AS source USING (source_id)
            JOIN source_snapshots AS current ON current.snapshot_id = source.snapshot_id
            JOIN snapshot_bundle_members AS member
              ON member.logical_source_id = current.logical_source_id
             AND member.member_role = 'subagent_transcript'
            JOIN snapshot_bundles AS bundle
              ON bundle.snapshot_bundle_id = member.snapshot_bundle_id
            WHERE child.is_sidechain AND child.metadata_json LIKE '%agent-tool-1%'
              AND member.snapshot_id != bundle.primary_snapshot_id
            ORDER BY bundle.captured_at DESC
            LIMIT 1
            """
        ).fetchone()
    assert child_row is not None
    assert child_row[2] != child_row[4]
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE session_sources SET snapshot_id = ?, snapshot_bundle_id = ? "
            "WHERE source_id = ?",
            [child_row[2], child_row[5], child_row[1]],
        )
    child_source = store.load_snapshot_source(str(child_row[2]))
    assert child_source is not None
    latest_child = store.capture_source(child_source, b"newer unnormalized child capture")

    with duckdb.connect(str(database), read_only=True) as connection:
        reference = _child_capture_reference(connection, str(child_row[0]))

    assert reference == (latest_child.snapshot_id, str(child_row[3]))


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
    newer = store.capture_source(child_source, b"newer-unparsed-child")

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
    dependencies = store.snapshot_dependencies(newer.snapshot_id)
    assert dependencies.derived_row_counts["episode_topology_projections"] == 1
    assert dependencies.derived_row_counts["episode_topology_projection_unavailable_children"] == 1

    store.prune_snapshot(newer.snapshot_id, force=True)

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


def test_parent_topology_projects_ambiguous_child_delegation(tmp_path) -> None:
    store, parent_session_id = persisted_conflicting_bundle(tmp_path)
    child_source = SessionSource(
        source_id="ambiguous-child-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/ambiguous-child.jsonl",
    )
    captured = store.capture_source(child_source, b"ambiguous-child")
    child_bundle = store.create_single_source_bundle(
        child_source,
        captured,
        "ambiguous-child-native",
    )
    store.record_lifecycle(child_bundle.snapshot_bundle_id, terminal_observed=True)
    child_session_id = "ambiguous-child-session"
    store.insert_parsed_bundle(
        child_source,
        ParsedSessionBundle(
            session=Session(
                session_id=child_session_id,
                source_id=child_source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="ambiguous-child-native",
                parent_session_id=parent_session_id,
                is_sidechain=True,
                metadata={"parent_link_status": "ambiguous"},
            ),
            raw_events=[
                RawEvent(
                    event_id="ambiguous-child-event",
                    source_id=child_source.source_id,
                    agent_name=AgentName.CODEX,
                    record_index=0,
                )
            ],
        ),
        captured,
        child_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )

    parent = analyze_session_episodes(store, parent_session_id, store.database_path)

    assert parent.topology_projection is not None
    assert parent.topology_projection.unavailable_children == []
    assert len(parent.topology_projection.delegations) == 1
    assert parent.topology_projection.delegations[0].status is DelegationStatus.AMBIGUOUS
    assert parent.topology_projection.delegations[0].parent_session_id == parent_session_id


def test_delegated_fallback_keeps_source_local_order_and_warning_source_identity() -> None:
    session = Session(
        session_id="delegated-source-local",
        source_id="source-child",
        agent_name=AgentName.CODEX,
        is_sidechain=True,
    )
    parsed = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id="foreign-event",
                source_id="source-foreign",
                agent_name=AgentName.CODEX,
                record_index=0,
            ),
            RawEvent(
                event_id="child-second",
                source_id="source-child",
                agent_name=AgentName.CODEX,
                record_index=2,
            ),
            RawEvent(
                event_id="child-first",
                source_id="source-child",
                agent_name=AgentName.CODEX,
                record_index=1,
            ),
        ],
    )
    selected = AnalysisInput(
        stored=StoredNormalization(
            run=NormalizationRun(
                normalization_run_id="normalization-test",
                bundle_content_id="content-test",
                snapshot_bundle_id="bundle-test",
                adapter_name="codex",
                adapter_version=CodexAdapter.version,
                normalization_version="normalization-v3",
                configuration_hash="configuration-test",
            ),
            source=SessionSource(
                source_id=session.source_id,
                agent_name=session.agent_name,
                source_path="/sessions/child.jsonl",
            ),
            bundle=parsed,
        ),
        lifecycle=LifecycleObservation(
            lifecycle_observation_id="lifecycle-test",
            snapshot_bundle_id="bundle-test",
            state="terminal_observed",
            observed_at=datetime(2026, 7, 14, tzinfo=UTC),
            evidence={},
        ),
        lifecycle_policy_version="lifecycle-v1",
    )
    segmented = EpisodeAnalysis(
        analysis_identity="unpersisted",
        normalization_run_id="unpersisted",
        segmentation_version="segmentation-v2",
        session_id=session.session_id,
        lifecycle_observation_id="lifecycle-test",
        lifecycle_state="terminal_observed",
    )

    episodes = _materialize_source_episodes(segmented, selected)

    assert episodes[0].event_anchor_ids == ["child-first", "child-second"]
    assert (
        _warning_anchor(
            {"source_id": "source-child", "record_index": 0},
            {
                ("source-foreign", 0): "foreign-event",
                ("source-child", 0): "child-event",
            },
        )
        == "child-event"
    )


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


def test_prune_includes_child_with_unavailable_parent_analysis_and_descendants(tmp_path) -> None:
    store, parent_session_id = persisted_conflicting_bundle(tmp_path)
    parent_source = SessionSource(
        source_id="source-episodes",
        agent_name=AgentName.CODEX,
        source_path="/sessions/episodes.jsonl",
    )
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        parent_snapshot_row = connection.execute(
            "SELECT snapshot_id FROM session_sources WHERE source_id = ?",
            [parent_source.source_id],
        ).fetchone()
    assert parent_snapshot_row is not None
    parent_snapshot_id = str(parent_snapshot_row[0])

    child_source = SessionSource(
        source_id="prune-child-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/prune-child.jsonl",
    )
    child_capture = store.capture_source(child_source, b"prune child")
    child_bundle = store.create_single_source_bundle(
        child_source,
        child_capture,
        "prune-child-native",
    )
    store.record_lifecycle(child_bundle.snapshot_bundle_id, terminal_observed=True)
    child_session_id = "prune-child-session"
    store.insert_parsed_bundle(
        child_source,
        ParsedSessionBundle(
            session=Session(
                session_id=child_session_id,
                source_id=child_source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="prune-child-native",
                parent_session_id=parent_session_id,
                is_sidechain=True,
                metadata={
                    "parent_link_status": "linked",
                    "subagent_metadata": {"tool_use_id": "native-tool-first"},
                },
            ),
            raw_events=[
                RawEvent(
                    event_id="prune-child-event",
                    source_id=child_source.source_id,
                    agent_name=AgentName.CODEX,
                    record_index=0,
                )
            ],
        ),
        child_capture,
        child_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    grandchild_source = SessionSource(
        source_id="prune-grandchild-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/prune-grandchild.jsonl",
    )
    grandchild_capture = store.capture_source(grandchild_source, b"prune grandchild")
    grandchild_bundle = store.create_single_source_bundle(
        grandchild_source,
        grandchild_capture,
        "prune-grandchild-native",
    )
    store.record_lifecycle(grandchild_bundle.snapshot_bundle_id, terminal_observed=True)
    grandchild_session_id = "prune-grandchild-session"
    store.insert_parsed_bundle(
        grandchild_source,
        ParsedSessionBundle(
            session=Session(
                session_id=grandchild_session_id,
                source_id=grandchild_source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="prune-grandchild-native",
                parent_session_id=child_session_id,
                is_sidechain=True,
                metadata={
                    "parent_link_status": "linked",
                    "subagent_metadata": {"tool_use_id": "missing-child-spawn"},
                },
            ),
            raw_events=[
                RawEvent(
                    event_id="prune-grandchild-event",
                    source_id=grandchild_source.source_id,
                    agent_name=AgentName.CODEX,
                    record_index=0,
                )
            ],
        ),
        grandchild_capture,
        grandchild_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    empty_child_source = SessionSource(
        source_id="prune-empty-child-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/prune-empty-child.jsonl",
    )
    empty_child_capture = store.capture_source(empty_child_source, b"prune empty child")
    empty_child_bundle = store.create_single_source_bundle(
        empty_child_source,
        empty_child_capture,
        "prune-empty-child-native",
    )
    store.record_lifecycle(empty_child_bundle.snapshot_bundle_id, terminal_observed=True)
    empty_child_session_id = "prune-empty-child-session"
    store.insert_parsed_bundle(
        empty_child_source,
        ParsedSessionBundle(
            session=Session(
                session_id=empty_child_session_id,
                source_id=empty_child_source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="prune-empty-child-native",
                parent_session_id=parent_session_id,
                is_sidechain=True,
                metadata={"parent_link_status": "missing"},
            )
        ),
        empty_child_capture,
        empty_child_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    empty_descendant_source = SessionSource(
        source_id="prune-empty-descendant-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/prune-empty-descendant.jsonl",
    )
    empty_descendant_capture = store.capture_source(
        empty_descendant_source,
        b"prune empty descendant",
    )
    empty_descendant_bundle = store.create_single_source_bundle(
        empty_descendant_source,
        empty_descendant_capture,
        "prune-empty-descendant-native",
    )
    store.record_lifecycle(empty_descendant_bundle.snapshot_bundle_id, terminal_observed=True)
    empty_descendant_session_id = "prune-empty-descendant-session"
    store.insert_parsed_bundle(
        empty_descendant_source,
        ParsedSessionBundle(
            session=Session(
                session_id=empty_descendant_session_id,
                source_id=empty_descendant_source.source_id,
                agent_name=AgentName.CODEX,
                native_session_id="prune-empty-descendant-native",
                parent_session_id=empty_child_session_id,
                is_sidechain=True,
                metadata={"parent_link_status": "missing"},
            ),
            raw_events=[
                RawEvent(
                    event_id="prune-empty-descendant-event",
                    source_id=empty_descendant_source.source_id,
                    agent_name=AgentName.CODEX,
                    record_index=0,
                )
            ],
        ),
        empty_descendant_capture,
        empty_descendant_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    store.capture_source(parent_source, b"newer unnormalized parent capture")
    child = analyze_session_episodes(store, child_session_id, store.database_path)
    empty_child = analyze_session_episodes(
        store,
        empty_child_session_id,
        store.database_path,
    )
    assert child.delegations[0].parent_session_id == parent_session_id
    assert child.delegations[0].parent_analysis_identity is None
    assert empty_child.episodes == []
    assert empty_child.delegations == []
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        grandchild_analysis_row = connection.execute(
            "SELECT child_analysis_identity FROM episode_delegations WHERE parent_session_id = ?",
            [child_session_id],
        ).fetchone()
        empty_descendant_analysis_row = connection.execute(
            "SELECT analysis_identity FROM episode_analysis_runs WHERE session_id = ?",
            [empty_descendant_session_id],
        ).fetchone()
    assert grandchild_analysis_row is not None
    assert empty_descendant_analysis_row is not None
    grandchild_analysis_id = str(grandchild_analysis_row[0])
    empty_descendant_analysis_id = str(empty_descendant_analysis_row[0])

    dependencies = store.snapshot_dependencies(parent_snapshot_id)

    assert child.analysis_identity in dependencies.analysis_run_ids
    assert grandchild_analysis_id in dependencies.analysis_run_ids
    assert empty_child.analysis_identity in dependencies.analysis_run_ids
    assert empty_descendant_analysis_id in dependencies.analysis_run_ids
    assert dependencies.derived_row_counts["episode_topology_projections"] >= 1
    store.prune_snapshot(parent_snapshot_id, force=True)
    with duckdb.connect(str(store.database_path), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM episode_analysis_runs WHERE analysis_identity = ?",
            [child.analysis_identity],
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM episode_topology_projections WHERE analysis_identity = ?",
            [child.analysis_identity],
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM episode_analysis_runs "
            "WHERE analysis_identity IN (SELECT unnest(?))",
            [[empty_child.analysis_identity, empty_descendant_analysis_id]],
        ).fetchone() == (0,)


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
