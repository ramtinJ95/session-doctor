from __future__ import annotations

import json
from pathlib import Path

import duckdb

from session_doctor.adapters import CodexAdapter
from session_doctor.analysis_workflow import analyze_session
from session_doctor.graph_projection import project_graph
from session_doctor.ids import source_id_for_path
from session_doctor.report_payload import build_session_report
from session_doctor.schemas import AgentName, SessionSource
from session_doctor.store import TABLE_NAMES, DuckDBStore

FIXTURE = Path(__file__).parent / "fixtures/codex/current-response-items.jsonl"


def test_current_codex_response_commands_survive_store_analysis_report_and_graph(
    tmp_path,
) -> None:
    source = SessionSource(
        source_id=source_id_for_path(AgentName.CODEX, FIXTURE),
        agent_name=AgentName.CODEX,
        source_path=str(FIXTURE),
    )
    bundle = CodexAdapter().parse_live_source(source)
    assert bundle.session is not None
    store = DuckDBStore(tmp_path / "current-codex.duckdb")
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

    before = total_rows(store.database_path)
    report = build_session_report(snapshot)
    repeated_report = build_session_report(snapshot)
    graph = project_graph(snapshot)
    repeated_graph = project_graph(snapshot)
    after = total_rows(store.database_path)
    graph_node_ids = {node.node_id for node in graph.nodes}
    command_edges = [edge for edge in graph.edges if edge.edge_type == "runs_command"]
    report_json = report.model_dump_json()
    graph_json = graph.model_dump_json()
    serialized = report_json + graph_json

    assert len(snapshot.normalized.command_runs) == 4
    assert graph.counts.nodes_by_type["command_run"] == 4
    assert graph.counts.nodes_by_type["tool_call"] == 6
    assert graph.counts.nodes_by_type["tool_result"] == 6
    assert len(command_edges) == 4
    assert all(edge.source_node_id in graph_node_ids for edge in graph.edges)
    assert all(edge.target_node_id in graph_node_ids for edge in graph.edges)
    assert len(graph_node_ids) == len(graph.nodes)
    assert len({edge.edge_id for edge in graph.edges}) == len(graph.edges)
    assert report.model_dump_json() == repeated_report.model_dump_json()
    assert graph.model_dump_json() == repeated_graph.model_dump_json()
    assert before == after
    assert "Synthetic failing output" not in serialized
    assert "Synthetic opaque exec output" not in serialized
    assert "synthetic-chunk" not in serialized
    assert "Synthetic inter-agent message" not in serialized
    assert "synthetic-agent-a" not in serialized
    assert "synthetic_tool" not in serialized
    assert "synthetic-server" not in serialized
    assert "synthetic-result" not in serialized
    assert "max_output_tokens" not in serialized
    assert "arguments_hash" not in serialized
    assert "output_hash" not in serialized
    output_keys = recursive_keys(json.loads(report_json)) | recursive_keys(json.loads(graph_json))
    assert output_keys.isdisjoint({"metadata", "arguments_hash", "output_hash"})


def total_rows(database_path: Path) -> int:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        total = 0
        for table_name in TABLE_NAMES:
            row = connection.execute(f"SELECT count(*) FROM {table_name}").fetchone()
            assert row is not None
            total += row[0]
        return total


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | {
            key for child in value.values() for key in recursive_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in recursive_keys(child)}
    return set()
