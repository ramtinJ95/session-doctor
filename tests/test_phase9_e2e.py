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
    sidechain_id = None
    sidechain_parent_id = None
    for session_id, agent, is_sidechain, parent_id in rows:
        if not is_sidechain:
            top_level_ids.setdefault(str(agent), str(session_id))
        elif sidechain_id is None:
            sidechain_id = str(session_id)
            sidechain_parent_id = str(parent_id)
    assert set(top_level_ids) == {"codex", "claude", "pi"}
    assert sidechain_id is not None
    assert sidechain_parent_id is not None

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
        assert terminal.exit_code == 0
        assert markdown.exit_code == 0
        assert markdown.stdout.startswith("# Session report")
        assert report_json.exit_code == 0
        report_payload = cast("dict[str, Any]", json.loads(report_json.stdout))
        assert report_payload["schema_version"] == 1
        assert report_payload["analysis"]["status"] == "current"
        assert report_payload["privacy"]["message_text_included"] is False
        assert_graph_payload(graph_json, store, session_id)

    sidechain_report = runner.invoke(
        app,
        ["report", sidechain_id, "--db", str(database_path), "--format", "json"],
    )
    sidechain_graph = runner.invoke(app, ["graph", sidechain_id, "--db", str(database_path)])
    assert sidechain_report.exit_code == 0
    sidechain_report_payload = cast("dict[str, Any]", json.loads(sidechain_report.stdout))
    assert sidechain_report_payload["session"]["is_sidechain"] is True
    assert sidechain_report_payload["session"]["parent_session_id"] == sidechain_parent_id
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
