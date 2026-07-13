from __future__ import annotations

import json
from pathlib import Path

import pytest
from analysis.fixtures import analysis_fixture_bundle
from pydantic import ValidationError
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.claude import ClaudeCodeAdapter
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.adapters.pi import PiAdapter
from session_doctor.analysis_workflow import analyze_session
from session_doctor.cli import app
from session_doctor.graph_projection import EDGE_TYPE_ORDER, NODE_TYPE_ORDER, project_graph
from session_doctor.ids import source_id_for_path, stable_id
from session_doctor.schemas import (
    AgentName,
    AnalysisRun,
    CommandRun,
    Message,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    SessionFeature,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import TABLE_NAMES, DuckDBStore

runner = CliRunner()
FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_graph_projects_complete_supported_rows_with_stable_endpoints(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    graph = project_graph(snapshot)
    repeated = project_graph(snapshot)
    node_ids = {node.node_id for node in graph.nodes}

    assert graph.schema_version == 1
    assert graph.directed is True
    assert graph.multigraph is True
    assert graph.privacy.message_text_included is False
    assert graph.counts.nodes_by_type["raw_event"] == len(snapshot.normalized.raw_events)
    assert graph.counts.nodes_by_type["message"] == len(snapshot.normalized.messages)
    assert graph.counts.nodes_by_type["tool_result"] == len(snapshot.normalized.tool_results)
    assert graph.counts.nodes_by_type["command_run"] == len(snapshot.normalized.command_runs)
    assert graph.counts.nodes_by_type["file_activity"] == len(snapshot.normalized.file_activities)
    assert graph.counts.nodes_by_type["file"] == 1
    assert graph.excluded.rows_by_type["model_usage"] == len(snapshot.normalized.model_usage)
    assert all(edge.source_node_id in node_ids for edge in graph.edges)
    assert all(edge.target_node_id in node_ids for edge in graph.edges)
    assert len(node_ids) == len(graph.nodes)
    assert len({edge.edge_id for edge in graph.edges}) == len(graph.edges)
    assert graph.model_dump_json() == repeated.model_dump_json()
    assert set(graph.counts.nodes_by_type) == set(NODE_TYPE_ORDER)
    assert set(graph.counts.edges_by_type) == set(EDGE_TYPE_ORDER)

    without_anchor = graph.model_dump()
    without_anchor["nodes"] = [
        node for node in without_anchor["nodes"] if node["node_type"] != "session"
    ]
    without_anchor["counts"]["nodes"] -= 1
    without_anchor["counts"]["nodes_by_type"]["session"] = 0
    with pytest.raises(ValidationError, match="exactly one session anchor"):
        type(graph).model_validate(without_anchor)


def test_graph_uses_only_conservative_supported_relations(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    graph = project_graph(snapshot)
    edge_types = {edge.edge_type for edge in graph.edges}

    assert {
        "contains",
        "derived_from",
        "targets_file",
        "member_of_failure_group",
        "repeats_request_of",
        "detected_in",
        "contributes_to_score",
        "supports_classification",
    }.issubset(edge_types)
    assert edge_types.isdisjoint(
        {
            "corrects",
            "causes_retry",
            "caused_classification",
            "caused_outcome",
            "same_failure_as",
        }
    )
    failure_edges = [edge for edge in graph.edges if edge.edge_type == "member_of_failure_group"]
    assert (
        len(failure_edges)
        <= (graph.counts.nodes_by_type["command_run"] + graph.counts.nodes_by_type["tool_result"])
        * graph.counts.nodes_by_type["failure_group"]
    )


def test_graph_payload_does_not_expose_private_normalized_fields(tmp_path) -> None:
    store, session_id = analyzed_store(tmp_path)
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None

    graph = project_graph(snapshot)
    serialized = graph.model_dump_json()
    message_payloads = [node.model_dump() for node in graph.nodes if node.node_type == "message"]

    assert all("text" not in node for node in message_payloads)
    assert "Please fix the failing pytest" not in serialized
    assert "I will run the tests" not in serialized
    assert "hash-failure" not in serialized
    assert "tool-error-hash" not in serialized
    assert "/private/source.jsonl" not in serialized
    assert "metadata" not in serialized
    assert "arguments_hash" not in serialized
    assert "output_hash" not in serialized
    assert "content_hash" not in serialized


def test_graph_excludes_stale_analysis_nodes(tmp_path) -> None:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "stale.duckdb")
    store.insert_untracked_parsed_bundle(source_for_bundle(), bundle)
    stale_run = AnalysisRun(
        analysis_run_id="stale-run",
        session_id=bundle.session.session_id,
        analyzer_version="phase5",
    )
    store.replace_analysis_rows(
        stale_run,
        [],
        [
            SessionFeature(
                session_feature_id="stale-feature",
                analysis_run_id=stale_run.analysis_run_id,
                session_id=bundle.session.session_id,
                feature_name="friction_score",
                feature_value="1",
                score=1,
            )
        ],
        [],
    )
    snapshot = store.load_diagnostic_snapshot(bundle.session.session_id)
    assert snapshot is not None

    graph = project_graph(snapshot)

    assert graph.analysis.status == "stale"
    assert graph.counts.nodes_by_type["message_feature"] == 0
    assert graph.counts.nodes_by_type["session_feature"] == 0
    assert graph.counts.nodes_by_type["classification"] == 0


def test_graph_projects_direct_relation_directions_and_exact_topology_scope(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "relations.duckdb")
    insert_empty_session(store, "root", "root-source")
    selected = Session(
        session_id="selected",
        source_id="selected-source",
        agent_name=AgentName.CLAUDE,
        parent_session_id="root",
        is_sidechain=True,
    )
    store.insert_untracked_parsed_bundle(
        SessionSource(
            source_id=selected.source_id,
            agent_name=selected.agent_name,
            source_path="/private/selected.jsonl",
        ),
        ParsedSessionBundle(
            session=selected,
            raw_events=[
                RawEvent(
                    event_id="event-1",
                    source_id=selected.source_id,
                    agent_name=selected.agent_name,
                    record_index=1,
                ),
                RawEvent(
                    event_id="event-2",
                    source_id=selected.source_id,
                    agent_name=selected.agent_name,
                    record_index=2,
                ),
            ],
            messages=[
                Message(
                    message_id="message-1",
                    session_id=selected.session_id,
                    role=NormalizedRole.USER,
                    source_event_id="event-1",
                    text="PRIVATE_PARENT_TEXT",
                    text_length=19,
                ),
                Message(
                    message_id="message-2",
                    session_id=selected.session_id,
                    role=NormalizedRole.ASSISTANT,
                    source_event_id="event-2",
                    parent_message_id="message-1",
                    text="PRIVATE_CHILD_TEXT",
                    text_length=18,
                ),
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
                    source_event_id="event-2",
                    tool_call_id="call-1",
                )
            ],
            command_runs=[
                CommandRun(
                    command_run_id="command-1",
                    session_id=selected.session_id,
                    source_event_id="event-2",
                    tool_call_id="call-1",
                    command="echo PRIVATE_COMMAND",
                )
            ],
            parse_warnings=[
                ParseWarning(
                    warning_id="warning-exact",
                    source_id=selected.source_id,
                    record_index=2,
                    message="PRIVATE_WARNING",
                    metadata={"code": "exact_warning"},
                ),
                ParseWarning(
                    warning_id="warning-unresolved",
                    source_id=selected.source_id,
                    record_index=99,
                    message="PRIVATE_WARNING",
                    metadata={"code": "unresolved_warning"},
                ),
            ],
        ),
    )
    insert_empty_session(store, "child", "child-source", parent_session_id="selected")
    snapshot = store.load_diagnostic_snapshot("selected")
    assert snapshot is not None

    graph = project_graph(snapshot)

    def id_for(node_type: str, field: str, value: str) -> str:
        return next(
            node.node_id
            for node in graph.nodes
            if node.node_type == node_type and getattr(node, field, None) == value
        )

    edge_pairs = {
        (edge.edge_type, edge.source_node_id, edge.target_node_id) for edge in graph.edges
    }
    session_node = next(node.node_id for node in graph.nodes if node.node_type == "session")
    raw_event_2 = next(
        node.node_id
        for node in graph.nodes
        if node.node_type == "raw_event" and node.source_event_id == "event-2"
    )

    assert (
        "parent_message",
        id_for("message", "message_id", "message-2"),
        id_for("message", "message_id", "message-1"),
    ) in edge_pairs
    assert (
        "has_tool_result",
        id_for("tool_call", "tool_call_id", "call-1"),
        id_for("tool_result", "tool_result_id", "result-1"),
    ) in edge_pairs
    assert (
        "runs_command",
        id_for("tool_call", "tool_call_id", "call-1"),
        id_for("command_run", "command_run_id", "command-1"),
    ) in edge_pairs
    assert (
        "has_warning",
        raw_event_2,
        id_for("parse_warning", "warning_id", "warning-exact"),
    ) in edge_pairs
    assert (
        "has_warning",
        session_node,
        id_for("parse_warning", "warning_id", "warning-unresolved"),
    ) in edge_pairs
    assert (
        "parent_session_reference",
        session_node,
        id_for("session_reference", "referenced_session_id", "root"),
    ) in edge_pairs
    assert (
        "child_session_reference",
        session_node,
        id_for("session_reference", "referenced_session_id", "child"),
    ) in edge_pairs
    assert graph.excluded.unresolved_references["has_warning"] == 1
    assert graph.counts.nodes_by_type["message"] == 2
    assert "PRIVATE_PARENT_TEXT" not in graph.model_dump_json()
    assert "PRIVATE_WARNING" not in graph.model_dump_json()

    invalid_topology = graph.model_dump()
    duplicate_edge = next(
        edge
        for edge in invalid_topology["edges"]
        if edge["edge_type"] == "parent_session_reference"
    ).copy()
    duplicate_edge["edge_id"] = stable_id("extra-topology-edge")
    invalid_topology["edges"].append(duplicate_edge)
    invalid_topology["counts"]["edges"] += 1
    invalid_topology["counts"]["edges_by_type"]["parent_session_reference"] += 1
    with pytest.raises(ValidationError, match="topology-only nodes"):
        type(graph).model_validate(invalid_topology)


def test_graph_cli_is_json_only_read_only_and_handles_missing_session(
    tmp_path, monkeypatch
) -> None:
    store, session_id = analyzed_store(tmp_path)
    before = {table: store.table_count(table) for table in TABLE_NAMES}

    result = runner.invoke(app, ["graph", session_id, "--db", str(store.database_path)])
    invalid = runner.invoke(
        app,
        ["graph", session_id, "--db", str(store.database_path), "--format", "dot"],
    )
    missing = runner.invoke(app, ["graph", "missing", "--db", str(store.database_path)])
    matching_agent = runner.invoke(
        app,
        ["graph", session_id, "--agent", "codex", "--db", str(store.database_path)],
    )

    def fail_snapshot_load(*args, **kwargs):
        raise AssertionError("mismatched diagnostic snapshot must not be loaded")

    monkeypatch.setattr(DuckDBStore, "load_diagnostic_snapshot", fail_snapshot_load)
    mismatched_agent = runner.invoke(
        app,
        ["graph", session_id, "--agent", "claude", "--db", str(store.database_path)],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["schema_version"] == 1
    assert invalid.exit_code == 2
    assert "Invalid --format" in invalid.stdout
    assert missing.exit_code == 1
    assert "Session not found: missing" in missing.stdout
    assert matching_agent.exit_code == 0
    assert mismatched_agent.exit_code == 1
    assert "belongs to codex, not claude" in mismatched_agent.stdout
    assert {table: store.table_count(table) for table in TABLE_NAMES} == before
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize(
    ("agent", "adapter", "fixture_path"),
    [
        (AgentName.CODEX, CodexAdapter(), FIXTURE_ROOT / "codex" / "basic-session.jsonl"),
        (
            AgentName.CLAUDE,
            ClaudeCodeAdapter(),
            FIXTURE_ROOT / "claude" / "basic-session.jsonl",
        ),
        (AgentName.PI, PiAdapter(), FIXTURE_ROOT / "pi" / "basic-session.jsonl"),
    ],
)
def test_graph_uses_adapter_neutral_vocabulary_for_native_fixtures(
    tmp_path, agent, adapter, fixture_path
) -> None:
    source = SessionSource(
        source_id=source_id_for_path(agent, fixture_path),
        agent_name=agent,
        source_path=str(fixture_path),
    )
    bundle = adapter.parse_live_source(source)
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / f"{agent.value}.duckdb")
    store.insert_untracked_parsed_bundle(source, bundle)
    analyze_session(
        store,
        bundle.session.session_id,
        store.database_path,
        artifact=None,
        no_artifact=True,
    )
    snapshot = store.load_diagnostic_snapshot(bundle.session.session_id)
    assert snapshot is not None

    graph = project_graph(snapshot)

    assert graph.counts.nodes_by_type["raw_event"] == len(bundle.raw_events)
    assert graph.counts.nodes_by_type["message"] == len(bundle.messages)
    assert graph.counts.nodes_by_type["tool_call"] == len(bundle.tool_calls)
    assert graph.counts.nodes_by_type["tool_result"] == len(bundle.tool_results)
    assert graph.counts.nodes_by_type["command_run"] == len(bundle.command_runs)
    assert graph.counts.nodes_by_type["file_activity"] == len(bundle.file_activities)
    assert graph.excluded.rows_by_type["model_usage"] == len(bundle.model_usage)
    assert {node.node_type for node in graph.nodes}.issubset(set(NODE_TYPE_ORDER))
    assert {edge.edge_type for edge in graph.edges}.issubset(set(EDGE_TYPE_ORDER))


def analyzed_store(tmp_path) -> tuple[DuckDBStore, str]:
    bundle = analysis_fixture_bundle()
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "graph.duckdb")
    store.insert_untracked_parsed_bundle(source_for_bundle(), bundle)
    analyze_session(
        store,
        bundle.session.session_id,
        store.database_path,
        artifact=None,
        no_artifact=True,
    )
    return store, bundle.session.session_id


def source_for_bundle() -> SessionSource:
    return SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/private/source.jsonl",
    )


def insert_empty_session(
    store: DuckDBStore,
    session_id: str,
    source_id: str,
    *,
    parent_session_id: str | None = None,
) -> None:
    session = Session(
        session_id=session_id,
        source_id=source_id,
        agent_name=AgentName.CLAUDE,
        parent_session_id=parent_session_id,
        is_sidechain=parent_session_id is not None,
    )
    store.insert_untracked_parsed_bundle(
        SessionSource(
            source_id=source_id,
            agent_name=session.agent_name,
            source_path=f"/private/{source_id}.jsonl",
        ),
        ParsedSessionBundle(session=session),
    )
