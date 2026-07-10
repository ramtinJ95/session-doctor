from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import duckdb
from typer.testing import CliRunner

from session_doctor.cli import app
from session_doctor.graph_projection import EDGE_TYPE_ORDER, NODE_TYPE_ORDER
from session_doctor.store import TABLE_NAMES, DuckDBStore

runner = CliRunner()
FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_native_three_adapter_reports_and_graphs_include_linked_sidechain(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    for agent in ("codex", "claude", "pi"):
        result = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                agent,
                "--source",
                str(FIXTURE_ROOT / agent / "repeated-failure-session.jsonl"),
                "--db",
                str(database_path),
            ],
        )
        assert result.exit_code == 0
    topology = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(FIXTURE_ROOT / "claude" / "topology"),
            "--db",
            str(database_path),
        ],
    )
    assert topology.exit_code == 0
    analysis = runner.invoke(
        app,
        ["analyze", "--all", "--db", str(database_path), "--format", "json"],
    )
    assert analysis.exit_code == 0
    assert json.loads(analysis.stdout)["counts"]["succeeded"] == 6

    store = DuckDBStore(database_path)
    before = {table: store.table_count(table) for table in TABLE_NAMES}
    with duckdb.connect(str(database_path), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT session_id, agent_name, is_sidechain, parent_session_id
            FROM sessions
            ORDER BY agent_name, is_sidechain, session_id
            """
        ).fetchall()
    top_level_ids: dict[str, str] = {}
    session_rows = {
        str(session_id): (str(agent), bool(is_sidechain), str(parent_id) if parent_id else None)
        for session_id, agent, is_sidechain, parent_id in rows
    }
    for session_id, agent, is_sidechain, _parent_id in rows:
        if not is_sidechain:
            top_level_ids.setdefault(str(agent), str(session_id))
    nested_sidechains = [
        session_id
        for session_id, (_, is_sidechain, parent_id) in session_rows.items()
        if is_sidechain
        and parent_id is not None
        and session_rows.get(parent_id, (None, False, None))[1]
    ]
    assert set(top_level_ids) == {"codex", "claude", "pi"}
    assert len(nested_sidechains) == 1
    sidechain_id = nested_sidechains[0]
    sidechain_parent_id = session_rows[sidechain_id][2]
    assert sidechain_parent_id is not None

    disclosed_evidence_texts = 0
    for session_id in top_level_ids.values():
        terminal = runner.invoke(app, ["report", session_id, "--db", str(database_path)])
        markdown = runner.invoke(
            app,
            ["report", session_id, "--db", str(database_path), "--format", "markdown"],
        )
        report_json = runner.invoke(
            app,
            ["report", session_id, "--db", str(database_path), "--format", "json"],
        )
        graph_json = runner.invoke(app, ["graph", session_id, "--db", str(database_path)])
        show_text = runner.invoke(
            app,
            [
                "report",
                session_id,
                "--db",
                str(database_path),
                "--format",
                "json",
                "--show-text",
            ],
        )
        assert terminal.exit_code == 0
        assert markdown.exit_code == 0
        assert markdown.stdout.startswith("# Session report")
        assert report_json.exit_code == 0
        report_payload = cast("dict[str, Any]", json.loads(report_json.stdout))
        assert report_payload["schema_version"] == 1
        assert report_payload["analysis"]["status"] == "current"
        assert report_payload["privacy"]["message_text_included"] is False
        snapshot = store.load_diagnostic_snapshot(session_id)
        assert snapshot is not None
        disclosed_evidence_texts += assert_report_privacy(
            snapshot,
            (terminal.stdout, markdown.stdout, report_json.stdout),
            report_json.stdout,
            show_text,
            graph_json.stdout,
        )
        assert_graph_payload(graph_json, store, session_id)

    sidechain_report = runner.invoke(
        app,
        ["report", sidechain_id, "--db", str(database_path), "--format", "json"],
    )
    sidechain_graph = runner.invoke(app, ["graph", sidechain_id, "--db", str(database_path)])
    sidechain_show_text = runner.invoke(
        app,
        [
            "report",
            sidechain_id,
            "--db",
            str(database_path),
            "--format",
            "json",
            "--show-text",
        ],
    )
    assert sidechain_report.exit_code == 0
    sidechain_report_payload = cast("dict[str, Any]", json.loads(sidechain_report.stdout))
    assert sidechain_report_payload["session"]["is_sidechain"] is True
    assert sidechain_report_payload["session"]["parent_session_id"] == sidechain_parent_id
    sidechain_snapshot = store.load_diagnostic_snapshot(sidechain_id)
    assert sidechain_snapshot is not None
    disclosed_evidence_texts += assert_report_privacy(
        sidechain_snapshot,
        (sidechain_report.stdout,),
        sidechain_report.stdout,
        sidechain_show_text,
        sidechain_graph.stdout,
    )
    assert_graph_payload(sidechain_graph, store, sidechain_id)
    sidechain_graph_payload = cast("dict[str, Any]", json.loads(sidechain_graph.stdout))
    references = [
        node
        for node in sidechain_graph_payload["nodes"]
        if node["node_type"] == "session_reference"
    ]
    assert any(
        node["relationship"] == "parent" and node["referenced_session_id"] == sidechain_parent_id
        for node in references
    )
    parent_snapshot = store.load_diagnostic_snapshot(sidechain_parent_id)
    assert parent_snapshot is not None
    parent_message_ids = {row.message_id for row in parent_snapshot.normalized.messages}
    projected_message_ids = {
        node["message_id"]
        for node in sidechain_graph_payload["nodes"]
        if node["node_type"] == "message"
    }
    assert parent_message_ids.isdisjoint(projected_message_ids)
    assert disclosed_evidence_texts > 0

    assert {table: store.table_count(table) for table in TABLE_NAMES} == before
    assert not (tmp_path / "artifacts").exists()


def assert_graph_payload(result, store: DuckDBStore, session_id: str) -> None:
    assert result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert payload["schema_version"] == 1
    assert payload["analysis"]["status"] == "current"
    assert payload["privacy"] == {"message_text_included": False}
    nodes = payload["nodes"]
    edges = payload["edges"]
    node_ids = {node["node_id"] for node in nodes}
    assert len(node_ids) == len(nodes)
    assert len({edge["edge_id"] for edge in edges}) == len(edges)
    assert all(edge["source_node_id"] in node_ids for edge in edges)
    assert all(edge["target_node_id"] in node_ids for edge in edges)
    assert {node["node_type"] for node in nodes}.issubset(set(NODE_TYPE_ORDER))
    assert {edge["edge_type"] for edge in edges}.issubset(set(EDGE_TYPE_ORDER))
    snapshot = store.load_diagnostic_snapshot(session_id)
    assert snapshot is not None
    assert payload["counts"]["nodes_by_type"]["raw_event"] == len(snapshot.normalized.raw_events)
    assert payload["counts"]["nodes_by_type"]["message"] == len(snapshot.normalized.messages)
    assert payload["counts"]["nodes_by_type"]["tool_call"] == len(snapshot.normalized.tool_calls)
    assert payload["counts"]["nodes_by_type"]["tool_result"] == len(
        snapshot.normalized.tool_results
    )
    assert payload["counts"]["nodes_by_type"]["command_run"] == len(
        snapshot.normalized.command_runs
    )
    assert payload["counts"]["nodes_by_type"]["file_activity"] == len(
        snapshot.normalized.file_activities
    )
    assert payload["counts"]["nodes_by_type"]["parse_warning"] == len(
        snapshot.normalized.parse_warnings
    )
    assert payload["counts"]["nodes_by_type"]["message_feature"] == len(
        snapshot.analysis.message_features
    )
    assert payload["counts"]["nodes_by_type"]["session_feature"] == len(
        snapshot.analysis.session_features
    )
    assert payload["counts"]["nodes_by_type"]["classification"] == len(
        snapshot.analysis.classifications
    )
    assert payload["excluded"]["rows_by_type"]["model_usage"] == len(
        snapshot.normalized.model_usage
    )
    assert payload["counts"]["edges_by_type"]["targets_file"] == len(
        snapshot.normalized.file_activities
    )


def assert_report_privacy(
    snapshot,
    default_outputs,
    default_json_output,
    show_text_result,
    graph_output,
) -> int:
    assert show_text_result.exit_code == 0
    show_payload = cast("dict[str, Any]", json.loads(show_text_result.stdout))
    authorized_ids = {feature.message_id for feature in snapshot.analysis.message_features}
    disclosed = [
        item
        for section in show_payload["evidence"].values()
        for item in section["items"]
        if item.get("text") is not None
    ]
    expected_disclosed_ids = {
        item["message_id"]
        for section in show_payload["evidence"].values()
        for item in section["items"]
        if item["item_type"] == "message_signal"
        and item["message_id"] in snapshot.indexes.messages_by_id
    }
    assert {item["message_id"] for item in disclosed} == expected_disclosed_ids
    assert expected_disclosed_ids.issubset(authorized_ids)
    assert all(
        item["text"] == snapshot.indexes.messages_by_id[item["message_id"]].text
        for item in disclosed
    )
    private_texts = [message.text for message in snapshot.normalized.messages if message.text]
    for output in default_outputs:
        assert all(text not in output for text in private_texts)
    assert all(json.dumps(text)[1:-1] not in graph_output for text in private_texts)
    forbidden = (
        "PRIVATE_SUBAGENT_TASK",
        "PRIVATE_NESTED_TASK",
        "PRIVATE_ORPHAN_TASK",
        "PRIVATE_ORPHAN_TOOL_OUTPUT",
        "PRIVATE_PERSISTED_TOOL_OUTPUT",
    )
    all_outputs = (*default_outputs, show_text_result.stdout, graph_output)
    assert all(marker not in output for marker in forbidden for output in all_outputs)
    default_payload = cast("dict[str, Any]", json.loads(default_json_output))
    assert all(
        item.get("text") is None
        for section in default_payload["evidence"].values()
        for item in section["items"]
        if item["item_type"] == "message_signal"
    )
    output_keys = (
        recursive_keys(default_payload)
        | recursive_keys(show_payload)
        | recursive_keys(json.loads(graph_output))
    )
    assert output_keys.isdisjoint(
        {"arguments_hash", "output_hash", "content_hash", "source_path", "metadata"}
    )
    return len(disclosed)


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | {
            key for child in value.values() for key in recursive_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in recursive_keys(child)}
    return set()
