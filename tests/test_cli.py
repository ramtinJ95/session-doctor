from __future__ import annotations

import json
from datetime import datetime
from inspect import signature
from pathlib import Path
from shutil import copyfile, copytree, rmtree
from typing import Any, cast

import duckdb
import pytest
from typer.testing import CliRunner

from session_doctor import __version__
from session_doctor.adapters import (
    BaseAdapter,
    ClaudeCodeAdapter,
    ParsedSessionBundle,
    SourceReadError,
)
from session_doctor.adapters.base import CapturedAdapterMember
from session_doctor.analysis import ANALYZER_VERSION
from session_doctor.analysis_workflow import SessionAnalysisError
from session_doctor.cli import app
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, AnalysisRun, Session, SessionSource
from session_doctor.store import SCHEMA_VERSION, DuckDBStore, SnapshotPruneBlocked

runner = CliRunner()
CODEX_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"
CLAUDE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claude"
CLAUDE_TOPOLOGY_FIXTURE_DIR = CLAUDE_FIXTURE_DIR / "topology"
PI_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pi"


class RecoverableFailureAdapter(BaseAdapter):
    name = AgentName.CODEX
    display_name = "Recoverable test adapter"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path("/unused"),)

    def discover(self, root: Path | None = None) -> list[SessionSource]:
        assert root is not None
        return [
            SessionSource(
                source_id=source_id_for_path(self.name, path),
                agent_name=self.name,
                source_path=str(path),
            )
            for path in sorted(root.glob("*.jsonl"))
        ]

    def parse_source(
        self, source: SessionSource, source_bytes: bytes | None = None
    ) -> ParsedSessionBundle:
        source_path = Path(source.source_path)
        if source_path.stem.startswith("bad"):
            raise SourceReadError(source_path, "synthetic read failure")
        return ParsedSessionBundle(
            session=Session(
                session_id=f"session-{source_path.stem}",
                source_id=source.source_id,
                agent_name=self.name,
            )
        )


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "session-doctor" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_preserves_helper_facade_imports() -> None:
    from session_doctor import cli

    assert cli.scan_adapter_summary is not None
    assert "console" not in signature(cli.render_ingest_summary).parameters
    assert "console" not in signature(cli.render_analysis_summary).parameters


def test_db_info_reports_missing_temp_database(tmp_path) -> None:
    result = runner.invoke(app, ["db", "info", "--db", str(tmp_path / "missing.duckdb")])

    assert result.exit_code == 0
    assert "Exists" in result.stdout
    assert "no" in result.stdout


def test_doctor_rejects_existing_directory_as_database_path(tmp_path) -> None:
    result = runner.invoke(app, ["doctor", "--db", str(tmp_path)])

    assert result.exit_code == 1
    assert "Database path" in result.stdout
    assert "error" in result.stdout
    assert "Result: failed" in result.stdout


def test_db_init_rejects_existing_directory_as_database_path(tmp_path) -> None:
    result = runner.invoke(app, ["db", "init", "--db", str(tmp_path)])

    assert result.exit_code == 1
    assert "Invalid database path" in result.stdout


def test_db_info_rejects_existing_directory_as_database_path(tmp_path) -> None:
    result = runner.invoke(app, ["db", "info", "--db", str(tmp_path)])

    assert result.exit_code == 1
    assert "Invalid database path" in result.stdout


def test_db_init_accepts_existing_empty_database_file(tmp_path) -> None:
    database_path = tmp_path / "empty.duckdb"
    with duckdb.connect(str(database_path)):
        pass

    result = runner.invoke(app, ["db", "init", "--db", str(database_path)])

    assert result.exit_code == 0
    assert f"Schema version: {SCHEMA_VERSION}" in result.stdout


def test_stale_database_is_inspectable_but_operational_commands_require_rebuild(
    tmp_path,
) -> None:
    database_path = tmp_path / "stale.duckdb"
    with duckdb.connect(str(database_path)) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        connection.execute("INSERT INTO schema_migrations (version) VALUES (2)")

    info_result = runner.invoke(app, ["db", "info", "--db", str(database_path)])

    assert info_result.exit_code == 0
    assert "Schema version" in info_result.stdout
    assert "2" in info_result.stdout

    commands = (
        ["db", "init", "--db", str(database_path)],
        ["sessions", "list", "--db", str(database_path)],
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(CODEX_FIXTURE_DIR / "basic-session.jsonl"),
            "--db",
            str(database_path),
        ],
        ["analyze", "session-1", "--db", str(database_path)],
        ["summary", "--db", str(database_path)],
    )
    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 1
        assert "Incompatible database" in result.stdout
        assert f"expected {SCHEMA_VERSION}" in result.stdout
        assert "Delete it and recreate it" in result.stdout
        assert "BinderException" not in result.stdout


def test_invalid_database_file_reports_stable_cli_error(tmp_path) -> None:
    database_path = tmp_path / "invalid.duckdb"
    database_path.write_text("not a DuckDB database")
    commands = (
        ["db", "init", "--db", str(database_path)],
        ["db", "info", "--db", str(database_path)],
        ["sessions", "list", "--db", str(database_path)],
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(CODEX_FIXTURE_DIR / "basic-session.jsonl"),
            "--db",
            str(database_path),
        ],
        ["analyze", "session-1", "--db", str(database_path)],
        ["summary", "--db", str(database_path)],
    )

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 1
        assert "Invalid database" in result.stdout
        assert "could not be opened" in result.stdout
        assert "DuckDB database" in result.stdout
        assert "Incompatible database" not in result.stdout
        assert "IOException" not in result.stdout


def test_database_connection_failure_reports_stable_cli_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "unavailable.duckdb"
    database_path.touch()

    def fail_connection(*args: object, **kwargs: object) -> None:
        raise duckdb.ConnectionException("synthetic connection failure")

    monkeypatch.setattr("session_doctor.store.connection.duckdb.connect", fail_connection)

    result = runner.invoke(app, ["db", "info", "--db", str(database_path)])

    assert result.exit_code == 1
    assert "Invalid database" in result.stdout
    assert "opened as a DuckDB database" in result.stdout
    assert "synthetic connection failure" not in result.stdout


def test_adapters_list_without_scan() -> None:
    result = runner.invoke(app, ["adapters", "list"])

    assert result.exit_code == 0
    assert "Codex" in result.stdout
    assert "Claude Code" in result.stdout
    assert "Pi" in result.stdout


def test_ingest_codex_fixture_writes_database_and_prints_summary(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 0
    assert "Codex ingest" in result.stdout
    assert "Response item messages" in result.stdout
    assert "Event message fallbacks" in result.stdout
    store = DuckDBStore(database_path)
    assert store.table_count("sessions") == 1
    assert store.table_count("messages") == 2
    with duckdb.connect(str(database_path), read_only=True) as connection:
        snapshot_row = connection.execute(
            "SELECT snapshot_id, snapshot_bundle_id FROM session_sources"
        ).fetchone()
    assert snapshot_row is not None
    assert snapshot_row[0] is not None
    assert snapshot_row[1] is not None
    assert store.load_snapshot_bytes(str(snapshot_row[0])) == fixture_path.read_bytes()


def test_snapshots_cli_lists_replays_and_prunes_exact_history(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0

    list_result = runner.invoke(
        app,
        ["snapshots", "list", "--db", str(database_path), "--format", "json"],
    )
    assert list_result.exit_code == 0
    rows = json.loads(list_result.stdout)
    assert len(rows) == 1
    snapshot_id = rows[0]["snapshot_id"]
    assert rows[0]["is_latest"] is True
    assert rows[0]["lifecycle_state"] == "snapshot_incomplete"
    show_result = runner.invoke(
        app,
        ["snapshots", "show", snapshot_id, "--db", str(database_path)],
    )
    assert show_result.exit_code == 0
    assert json.loads(show_result.stdout)["members"][0]["member_role"] == "primary"

    replay_path = tmp_path / "replayed.jsonl"
    replay_result = runner.invoke(
        app,
        [
            "snapshots",
            "replay",
            snapshot_id,
            "--db",
            str(database_path),
            "--output",
            str(replay_path),
        ],
    )
    assert replay_result.exit_code == 0
    assert replay_path.read_bytes() == fixture_path.read_bytes()
    refused_overwrite = runner.invoke(
        app,
        [
            "snapshots",
            "replay",
            snapshot_id,
            "--db",
            str(database_path),
            "--output",
            str(replay_path),
        ],
    )
    assert refused_overwrite.exit_code == 2
    assert "--overwrite" in refused_overwrite.stderr

    blocked = runner.invoke(
        app,
        ["snapshots", "prune", snapshot_id, "--db", str(database_path)],
    )
    assert blocked.exit_code == 1
    assert "prune blocked" in blocked.stdout
    forced = runner.invoke(
        app,
        ["snapshots", "prune", snapshot_id, "--db", str(database_path), "--force"],
    )
    assert forced.exit_code == 0
    assert "Force prune dependencies" in forced.stdout
    assert '"sessions"' in forced.stdout
    assert DuckDBStore(database_path).table_count("source_snapshots") == 0


def test_malformed_trailing_record_keeps_snapshot_incomplete(tmp_path) -> None:
    source_path = tmp_path / "truncated.jsonl"
    source_path.write_bytes(
        (CODEX_FIXTURE_DIR / "basic-session.jsonl").read_bytes() + b'{"truncated":'
    )
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_path),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 0
    snapshots = DuckDBStore(database_path).list_snapshots()
    assert snapshots[0].lifecycle_state == "snapshot_incomplete"


def test_failed_multifile_preparation_keeps_every_snapshot_owned(tmp_path) -> None:
    source_path = tmp_path / "invalid-utf8.jsonl"
    source_path.write_bytes(b"\xff")
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_path),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code != 0
    store = DuckDBStore(database_path)
    snapshots = store.list_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].lifecycle_state == "snapshot_incomplete"
    with duckdb.connect(str(database_path), read_only=True) as connection:
        unowned = connection.execute(
            """
            SELECT count(*) FROM source_snapshots AS s
            LEFT JOIN snapshot_bundle_members AS m USING (snapshot_id)
            WHERE m.snapshot_id IS NULL
            """
        ).fetchone()
    assert unowned == (0,)


def test_claude_directory_skips_invalid_utf8_and_keeps_valid_capture(tmp_path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    copyfile(CLAUDE_FIXTURE_DIR / "basic-session.jsonl", source_dir / "valid.jsonl")
    (source_dir / "invalid.jsonl").write_bytes(b"\xff")
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_dir),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 1
    assert "Skipped source" in result.stdout
    assert DuckDBStore(database_path).table_count("sessions") == 1


def test_truncated_claude_child_keeps_root_bundle_incomplete(tmp_path) -> None:
    source_root = tmp_path / "topology"
    copytree(CLAUDE_TOPOLOGY_FIXTURE_DIR, source_root)
    child_path = source_root / "project/session-root/subagents/agent-a.jsonl"
    child_path.write_bytes(child_path.read_bytes() + b'\n{"truncated":')
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_root),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 0
    root_snapshot = next(
        row
        for row in DuckDBStore(database_path).list_snapshots()
        if row.source_path.endswith("session-root.jsonl")
    )
    assert root_snapshot.capture_status == "incomplete"
    assert root_snapshot.lifecycle_state == "snapshot_incomplete"


def test_invalid_utf8_claude_child_is_retained_as_incomplete_member(tmp_path) -> None:
    source_root = tmp_path / "topology"
    copytree(CLAUDE_TOPOLOGY_FIXTURE_DIR, source_root)
    child_path = source_root / "project/session-root/subagents/agent-a.jsonl"
    child_path.write_bytes(b"\xff")
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_root),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 1
    store = DuckDBStore(database_path)
    root_snapshot = next(
        row for row in store.list_snapshots() if row.source_path.endswith("session-root.jsonl")
    )
    child_member = next(
        member
        for member in store.load_bundle_members(root_snapshot.snapshot_bundle_id)
        if member.source_path == str(child_path)
    )
    assert root_snapshot.capture_status == "incomplete"
    assert child_member.source_bytes == b"\xff"


def test_ingest_resolves_source_path_before_deriving_ids(tmp_path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
        relative_source = Path("sessions") / "basic-session.jsonl"
        relative_source.parent.mkdir()
        copyfile(fixture_path, relative_source)
        database_path = Path("session-doctor.duckdb")

        relative_result = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                "codex",
                "--source",
                str(relative_source),
                "--db",
                str(database_path),
            ],
        )
        absolute_result = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                "codex",
                "--source",
                str(relative_source.resolve()),
                "--db",
                str(database_path),
            ],
        )

        assert relative_result.exit_code == 0
        assert absolute_result.exit_code == 0
        store = DuckDBStore(database_path)
        assert store.table_count("session_sources") == 1
        assert store.table_count("sessions") == 1


def test_ingest_rejects_unsupported_agent(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "unknown-agent",
            "--db",
            str(tmp_path / "session-doctor.duckdb"),
        ],
    )

    assert result.exit_code == 2
    assert "Unsupported --agent" in result.stdout


def test_ingest_claude_root_fixture_writes_and_replaces_normalized_rows(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CLAUDE_FIXTURE_DIR / "basic-session.jsonl"
    command = [
        "ingest",
        "--agent",
        "claude",
        "--source",
        str(fixture_path),
        "--db",
        str(database_path),
    ]

    first_result = runner.invoke(app, command)
    second_result = runner.invoke(app, command)

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    assert "Claude Code ingest" in second_result.stdout
    assert "Messages" in second_result.stdout
    assert "Tool calls" in second_result.stdout
    assert "File activities" in second_result.stdout
    store = DuckDBStore(database_path)
    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("raw_events") == 9
    assert store.table_count("messages") == 6
    assert store.table_count("tool_calls") == 5
    assert store.table_count("tool_results") == 2
    assert store.table_count("command_runs") == 1
    assert store.table_count("file_activities") == 3
    assert store.table_count("model_usage") == 2
    assert store.table_count("parse_warnings") == 3


def test_ingest_claude_directory_selects_root_and_subagent_sessions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_root = tmp_path / "projects"
    project_dir = claude_root / "project"
    session_dir = project_dir / "session-1"
    subagents_dir = session_dir / "subagents"
    tool_results_dir = session_dir / "tool-results"
    subagents_dir.mkdir(parents=True)
    tool_results_dir.mkdir()
    copyfile(CLAUDE_FIXTURE_DIR / "basic-session.jsonl", project_dir / "session-1.jsonl")
    (subagents_dir / "agent-a.jsonl").write_text("not a root transcript")
    (subagents_dir / "agent-a.meta.json").write_text("{}")
    (tool_results_dir / "result.txt").write_text("PRIVATE_SIDECAR_OUTPUT")
    (tool_results_dir / "result.jsonl").write_text(
        '{"type":"system","content":"PRIVATE_JSONL_SIDECAR_OUTPUT"}\n'
    )
    (project_dir / "memory.md").write_text("PRIVATE_MEMORY")
    monkeypatch.setattr(
        "session_doctor.adapters.claude.ClaudeCodeAdapter.default_roots",
        lambda self: (claude_root,),
    )
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        ["ingest", "--agent", "claude", "--db", str(database_path)],
    )

    assert result.exit_code == 0
    assert "Sources" in result.stdout
    assert "root_session=1" in result.stdout
    assert "subsession=1" in result.stdout
    assert "memory=1" in result.stdout
    assert "tool_result=2" in result.stdout
    store = DuckDBStore(database_path)
    assert store.table_count("session_sources") == 2
    assert store.table_count("sessions") == 2
    assert store.table_count("raw_events") == 9


def test_ingest_claude_accepts_explicit_subagent_source(tmp_path) -> None:
    subagents_dir = tmp_path / "session" / "subagents"
    subagents_dir.mkdir(parents=True)
    source_path = subagents_dir / "agent-a.jsonl"
    source_path.touch()

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_path),
            "--db",
            str(tmp_path / "session-doctor.duckdb"),
        ],
    )

    assert result.exit_code == 0
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    summaries = store.list_session_summaries()
    assert len(summaries) == 1
    bundle = store.load_session_bundle(summaries[0].session_id)
    assert bundle is not None
    assert bundle.session is not None
    assert bundle.session.is_sidechain is True


@pytest.mark.parametrize(
    ("directory_name", "filename", "content"),
    [
        (
            "tool-results",
            "result.jsonl",
            '{"type":"system","content":"PRIVATE_SIDECAR_OUTPUT"}\n',
        ),
    ],
)
def test_ingest_claude_direct_tool_result_directory_selects_no_sessions(
    tmp_path,
    directory_name: str,
    filename: str,
    content: str,
) -> None:
    source_dir = tmp_path / "session-1" / directory_name
    source_dir.mkdir(parents=True)
    (source_dir / filename).write_text(content)
    database_path = tmp_path / f"{directory_name}.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_dir),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 0
    assert not database_path.exists()


def test_ingest_claude_topology_replaces_and_analyzes_all_sessions(tmp_path) -> None:
    database_path = tmp_path / "claude-topology.duckdb"
    ingest_command = [
        "ingest",
        "--agent",
        "claude",
        "--source",
        str(CLAUDE_TOPOLOGY_FIXTURE_DIR),
        "--db",
        str(database_path),
    ]

    first_result = runner.invoke(app, ingest_command)
    second_result = runner.invoke(app, ingest_command)

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    assert "root_session=1" in second_result.stdout
    assert "subsession=2" in second_result.stdout
    assert "subagent_metadata=3" in second_result.stdout
    assert "tool_result=2" in second_result.stdout
    store = DuckDBStore(database_path)
    assert store.table_count("session_sources") == 3
    assert store.table_count("sessions") == 3
    assert store.table_count("raw_events") == 5
    with duckdb.connect(str(database_path), read_only=True) as connection:
        source_links = connection.execute(
            "SELECT source_kind, parent_source_id FROM session_sources ORDER BY source_path"
        ).fetchall()
        session_links = connection.execute(
            "SELECT is_sidechain, parent_session_id FROM sessions ORDER BY session_id"
        ).fetchall()
    assert sum(parent_source_id is not None for _, parent_source_id in source_links) == 2
    assert sum(parent_session_id is not None for _, parent_session_id in session_links) == 2
    assert sum(bool(is_sidechain) for is_sidechain, _ in session_links) == 2

    summaries = store.list_session_summaries()
    assert len(summaries) == 3
    for summary in summaries:
        analyze_result = runner.invoke(
            app,
            [
                "analyze",
                summary.session_id,
                "--db",
                str(database_path),
                "--no-artifact",
            ],
        )
        assert analyze_result.exit_code == 0

    summary_result = runner.invoke(
        app,
        ["summary", "--agent", "claude", "--db", str(database_path), "--format", "json"],
    )
    assert summary_result.exit_code == 0
    assert json.loads(summary_result.stdout)["totals"] == {
        "analyzed_sessions": 3,
        "sessions": 3,
        "unanalyzed_sessions": 0,
    }


def test_claude_multifile_bundle_replays_without_live_files(tmp_path) -> None:
    source_root = tmp_path / "topology"
    copytree(CLAUDE_TOPOLOGY_FIXTURE_DIR, source_root)
    database_path = tmp_path / "session-doctor.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(source_root),
            "--db",
            str(database_path),
        ],
    )
    assert result.exit_code == 0
    store = DuckDBStore(database_path)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        bundle_rows = [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                """
                SELECT snapshot_bundle_id, source_kind FROM session_sources
                WHERE source_kind IN ('root_session', 'subsession')
                ORDER BY source_path
                """
            ).fetchall()
        ]
    rmtree(source_root)

    adapter = ClaudeCodeAdapter()
    replayed_parent_ids: list[str | None] = []
    sidecar_correlated = False
    roles_by_kind: dict[str, set[str]] = {}
    root_member_paths: set[str] = set()
    for bundle_id, source_kind in bundle_rows:
        loaded = store.load_bundle_members(bundle_id)
        roles_by_kind[source_kind] = {member.member_role for member in loaded}
        if source_kind == "root_session":
            root_member_paths = {member.source_path for member in loaded}
        assert loaded[0].member_role == "primary"
        assert [member.capture_order for member in loaded] == list(range(len(loaded)))
        assert all(
            member.source_bytes is not None
            or member.member_capture_status in {"missing", "unreadable"}
            for member in loaded
        )
        primary = loaded[0]
        assert primary.source is not None
        assert primary.source_bytes is not None
        context = tuple(
            CapturedAdapterMember(member.source, member.member_role, member.source_bytes)
            for member in loaded
            if member.source is not None and member.source_bytes is not None
        )
        prepared = adapter.prepare_captured_source(primary.source, context)
        replayed = adapter.parse_source(prepared, primary.source_bytes)
        assert replayed.session is not None
        if source_kind == "subsession":
            replayed_parent_ids.append(replayed.session.parent_session_id)
        sidecar_correlated = sidecar_correlated or any(
            result.metadata.get("sidecar_correlated") is True for result in replayed.tool_results
        )
    assert all(parent_id is not None for parent_id in replayed_parent_ids)
    assert sidecar_correlated
    assert "subagent_transcript" in roles_by_kind["root_session"]
    assert not any(path.endswith("orphan.txt") for path in root_member_paths)
    assert any(path.endswith("agent-orphan.meta.json") for path in root_member_paths)
    assert len(roles_by_kind["subsession"] & {"related_transcript", "subagent_transcript"}) <= 1

    root_snapshot = next(
        member.source
        for bundle_id, source_kind in bundle_rows
        if source_kind == "root_session"
        for member in store.load_bundle_members(bundle_id)
        if member.member_role == "primary"
    )
    assert root_snapshot is not None
    root_summary = next(
        row for row in store.list_snapshots() if row.source_id == root_snapshot.source_id
    )
    bundle_output = tmp_path / "replayed-bundle"
    bundle_replay = runner.invoke(
        app,
        [
            "snapshots",
            "replay",
            root_summary.snapshot_id,
            "--db",
            str(database_path),
            "--output",
            str(bundle_output),
            "--bundle",
        ],
    )
    assert bundle_replay.exit_code == 0
    manifest = json.loads((bundle_output / "manifest.json").read_text())
    assert manifest[0]["member_role"] == "primary"
    assert any(row["member_role"] == "tool_result" for row in manifest)
    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(root_summary.snapshot_id)
    dependencies = store.snapshot_dependencies(root_summary.snapshot_id)
    assert dependencies.inbound_source_ids
    assert dependencies.inbound_session_ids
    prune_result = store.prune_snapshot(root_summary.snapshot_id, force=True)
    assert len(prune_result.dependent_source_ids) == 1
    assert store.table_count("snapshot_bundles") == 2
    assert store.table_count("sessions") == 2
    assert store.snapshot_summary(root_summary.snapshot_id) is None
    with duckdb.connect(str(database_path), read_only=True) as connection:
        remaining_parent_references = connection.execute(
            "SELECT count(*) FROM session_sources WHERE parent_source_id IN (SELECT unnest(?))",
            [list(dependencies.source_ids)],
        ).fetchone()
        remaining_session_parent_references = connection.execute(
            "SELECT count(*) FROM sessions WHERE parent_session_id IN (SELECT unnest(?))",
            [list(dependencies.session_ids)],
        ).fetchone()
    assert remaining_parent_references == (0,)
    assert remaining_session_parent_references == (0,)


def test_ingest_single_file_fails_immediately_on_recoverable_source_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "bad.jsonl"
    source_path.touch()
    monkeypatch.setattr(
        "session_doctor.cli.adapter_for_ingest",
        lambda agent: RecoverableFailureAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_path),
            "--db",
            str(tmp_path / "session-doctor.duckdb"),
        ],
    )

    assert result.exit_code == 1
    assert "Source failed" in result.stdout
    assert "source_read_error" in result.stdout
    assert "Skipped source" not in result.stdout


def test_ingest_directory_keeps_valid_sources_but_exits_nonzero_after_skip(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "sessions"
    source_dir.mkdir()
    (source_dir / "bad.jsonl").touch()
    (source_dir / "good.jsonl").touch()
    database_path = tmp_path / "session-doctor.duckdb"
    monkeypatch.setattr(
        "session_doctor.cli.adapter_for_ingest",
        lambda agent: RecoverableFailureAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_dir),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 1
    assert "Skipped source" in result.stdout
    assert "source_read_error" in result.stdout
    assert "Sessions" in result.stdout
    assert DuckDBStore(database_path).table_count("sessions") == 1


def test_ingest_directory_skips_invalid_utf8_and_processes_later_sources(tmp_path) -> None:
    source_dir = tmp_path / "sessions"
    source_dir.mkdir()
    (source_dir / "a-invalid.jsonl").write_bytes(b"\xff\n")
    copyfile(CODEX_FIXTURE_DIR / "basic-session.jsonl", source_dir / "z-valid.jsonl")
    database_path = tmp_path / "session-doctor.duckdb"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_dir),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 1
    assert "Skipped source" in result.stdout
    assert "source_format_error" in result.stdout
    assert "Unable to decode Codex source as UTF-8" in result.stdout
    assert "root_session=1" in result.stdout
    assert "root_session=2" not in result.stdout
    assert DuckDBStore(database_path).table_count("sessions") == 1


def test_ingest_directory_total_recoverable_failure_exits_nonzero(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "sessions"
    source_dir.mkdir()
    (source_dir / "bad.jsonl").touch()
    monkeypatch.setattr(
        "session_doctor.cli.adapter_for_ingest",
        lambda agent: RecoverableFailureAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_dir),
            "--db",
            str(tmp_path / "session-doctor.duckdb"),
        ],
    )

    assert result.exit_code == 1
    assert "Skipped sources" in result.stdout
    assert "Sessions" in result.stdout


def test_ingest_persistence_failure_aborts_without_skipping(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "good.jsonl"
    source_path.touch()
    monkeypatch.setattr(
        "session_doctor.cli.adapter_for_ingest",
        lambda agent: RecoverableFailureAdapter(),
    )

    def fail_persistence(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic persistence failure")

    monkeypatch.setattr(DuckDBStore, "insert_parsed_bundle", fail_persistence)

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_path),
            "--db",
            str(tmp_path / "session-doctor.duckdb"),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert "Skipped source" not in result.stdout
    assert "Source failed" not in result.stdout


def test_ingest_unexpected_parser_failure_aborts_without_skipping(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "good.jsonl"
    source_path.touch()
    adapter = RecoverableFailureAdapter()

    def fail_parse(source: SessionSource, source_bytes: bytes | None = None) -> ParsedSessionBundle:
        raise RuntimeError("synthetic parser bug")

    monkeypatch.setattr(adapter, "parse_source", fail_parse)
    monkeypatch.setattr("session_doctor.cli.adapter_for_ingest", lambda agent: adapter)

    database_path = tmp_path / "session-doctor.duckdb"
    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(source_path),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert "Skipped source" not in result.stdout
    assert "Source failed" not in result.stdout
    assert DuckDBStore(database_path).table_count("source_blobs") == 1
    assert DuckDBStore(database_path).table_count("source_snapshots") == 1
    assert DuckDBStore(database_path).table_count("snapshot_bundles") == 1
    assert DuckDBStore(database_path).table_count("snapshot_bundle_members") == 1


def test_ingest_pi_fixture_writes_database_and_prints_summary(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = PI_FIXTURE_DIR / "basic-session.jsonl"

    result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "pi",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )

    assert result.exit_code == 0
    assert "Pi ingest" in result.stdout
    assert "Response item messages" not in result.stdout
    assert "Event message fallbacks" not in result.stdout
    assert "Tool calls" in result.stdout
    assert "Tool results" in result.stdout
    assert "File activities" in result.stdout
    assert "Model usage rows" in result.stdout
    store = DuckDBStore(database_path)
    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("messages") == 4
    assert store.table_count("tool_calls") == 4
    assert store.table_count("tool_results") == 1
    assert store.table_count("command_runs") == 1
    assert store.table_count("file_activities") == 3
    assert store.table_count("model_usage") == 1


def test_ingest_pi_fixture_replaces_existing_source_records(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = PI_FIXTURE_DIR / "basic-session.jsonl"
    command = [
        "ingest",
        "--agent",
        "pi",
        "--source",
        str(fixture_path),
        "--db",
        str(database_path),
    ]

    first_result = runner.invoke(app, command)
    second_result = runner.invoke(app, command)

    assert first_result.exit_code == 0
    assert second_result.exit_code == 0
    store = DuckDBStore(database_path)
    assert store.table_count("session_sources") == 1
    assert store.table_count("sessions") == 1
    assert store.table_count("raw_events") == 15
    assert store.table_count("messages") == 4
    assert store.table_count("parse_warnings") == 3


def test_sessions_list_shows_ingested_codex_session(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0

    result = runner.invoke(app, ["sessions", "list", "--db", str(database_path)])

    assert result.exit_code == 0
    assert "Sessions" in result.stdout
    assert "codex" in result.stdout
    assert str(fixture_path) in result.stdout
    assert "Response Items" not in result.stdout
    assert "Event Fallbacks" not in result.stdout
    assert "Commands" in result.stdout


def test_sessions_list_filters_by_agent_and_rejects_unknown_agent(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    sources = (
        ("codex", CODEX_FIXTURE_DIR / "basic-session.jsonl"),
        ("pi", PI_FIXTURE_DIR / "basic-session.jsonl"),
    )
    for agent, fixture_path in sources:
        result = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                agent,
                "--source",
                str(fixture_path),
                "--db",
                str(database_path),
            ],
        )
        assert result.exit_code == 0

    codex_result = runner.invoke(
        app,
        ["sessions", "list", "--agent", "codex", "--db", str(database_path)],
    )
    pi_result = runner.invoke(
        app,
        ["sessions", "list", "--agent", "pi", "--db", str(database_path)],
    )
    invalid_result = runner.invoke(
        app,
        ["sessions", "list", "--agent", "unknown", "--db", str(database_path)],
    )

    assert codex_result.exit_code == 0
    assert str(sources[0][1]) in codex_result.stdout
    assert str(sources[1][1]) not in codex_result.stdout
    assert pi_result.exit_code == 0
    assert str(sources[1][1]) in pi_result.stdout
    assert str(sources[0][1]) not in pi_result.stdout
    assert invalid_result.exit_code == 2
    assert "Unsupported --agent" in invalid_result.stdout


def test_analyze_ingested_codex_session_writes_artifact_and_rows(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "repeated-failure-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0
    store = DuckDBStore(database_path)
    session_id = store.list_session_summaries()[0].session_id

    result = runner.invoke(app, ["analyze", session_id, "--db", str(database_path)])

    assert result.exit_code == 0
    assert "Session analysis" in result.stdout
    assert "Classifications" in result.stdout
    assert "friction_score" in result.stdout
    assert "stuckness_score" in result.stdout
    artifact_path = tmp_path / "artifacts" / f"{session_id}-analysis.json"
    assert artifact_path.exists()
    payload = cast("dict[str, Any]", json.loads(artifact_path.read_text()))
    assert payload["session"]["session_id"] == session_id
    assert payload["analysis_run"]["analyzer_version"] == "phase6"
    assert "failed_command_ratio" in payload["summary_metrics"]
    assert "friction_score" in payload["summary_metrics"]
    friction_score = payload_feature(payload, "session_features", "friction_score")
    assert friction_score["metadata"]["formula"] == "friction_score_v1"
    repeated_failure_evidence = payload_feature_evidence(
        payload, "session_features", "repeated_failure_count"
    )
    repeated_failure_groups = repeated_failure_evidence["groups"]
    assert isinstance(repeated_failure_groups, list)
    assert repeated_failure_groups
    assert all(
        isinstance(group, dict) and "group_type" in group for group in repeated_failure_groups
    )
    assert repeated_failure_evidence["source_event_ids"]
    labels = {classification["label"] for classification in payload["classifications"]}
    assert {"user_stuck", "tooling_blocked"}.issubset(labels)
    assert "agent_looping" not in labels
    user_stuck = next(
        classification
        for classification in payload["classifications"]
        if classification["label"] == "user_stuck"
    )
    assert user_stuck["metadata"]["score_feature"] == "stuckness_score"
    assert store.table_count("analysis_runs") == 1
    assert store.table_count("session_features") > 0
    assert store.table_count("session_classifications") > 0


def test_analyze_single_session_agent_guard_accepts_match_and_rejects_mismatch(
    tmp_path,
) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0
    store = DuckDBStore(database_path)
    session_id = store.list_session_summaries()[0].session_id

    matching_result = runner.invoke(
        app,
        [
            "analyze",
            session_id,
            "--agent",
            "codex",
            "--no-artifact",
            "--db",
            str(database_path),
        ],
    )
    before_mismatch = store.table_count("analysis_runs")
    mismatch_result = runner.invoke(
        app,
        ["analyze", session_id, "--agent", "pi", "--db", str(database_path)],
    )

    assert matching_result.exit_code == 0
    assert mismatch_result.exit_code == 1
    assert "Agent mismatch" in mismatch_result.stdout
    assert "belongs to codex, not pi" in mismatch_result.stdout
    assert store.table_count("analysis_runs") == before_mismatch
    assert not (tmp_path / "artifacts" / f"{session_id}-analysis.json").exists()


def test_analyze_ingested_pi_session_writes_artifact_and_rows(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = PI_FIXTURE_DIR / "repeated-failure-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "pi",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0
    store = DuckDBStore(database_path)
    session_id = store.list_session_summaries()[0].session_id

    result = runner.invoke(app, ["analyze", session_id, "--db", str(database_path)])

    assert result.exit_code == 0
    assert "Session analysis" in result.stdout
    assert "Classifications" in result.stdout
    assert "agent_fit_risk" in result.stdout
    artifact_path = tmp_path / "artifacts" / f"{session_id}-analysis.json"
    assert artifact_path.exists()
    payload = cast("dict[str, Any]", json.loads(artifact_path.read_text()))
    assert payload["session"]["session_id"] == session_id
    assert payload["session"]["agent_name"] == "pi"
    assert "failed_command_ratio" in payload["summary_metrics"]
    assert "project_complexity_signal" in payload["summary_metrics"]
    repeated_failure_evidence = payload_feature_evidence(
        payload, "session_features", "repeated_failure_count"
    )
    repeated_failure_groups = repeated_failure_evidence["groups"]
    assert isinstance(repeated_failure_groups, list)
    assert repeated_failure_groups
    assert all(
        isinstance(group, dict) and "group_type" in group for group in repeated_failure_groups
    )
    assert repeated_failure_evidence["source_event_ids"]
    labels = {classification["label"] for classification in payload["classifications"]}
    assert {"user_stuck", "tooling_blocked", "agent_looping"}.issubset(labels)
    assert store.table_count("analysis_runs") == 1
    assert store.table_count("session_features") > 0
    assert store.table_count("session_classifications") > 0


def test_claude_root_session_lists_analyzes_and_summarizes_in_terminal_and_json(
    tmp_path,
) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CLAUDE_FIXTURE_DIR / "basic-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "claude",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0
    session_id = DuckDBStore(database_path).list_session_summaries()[0].session_id

    sessions_result = runner.invoke(app, ["sessions", "list", "--db", str(database_path)])
    terminal_analysis = runner.invoke(
        app,
        ["analyze", session_id, "--db", str(database_path), "--no-artifact"],
    )
    json_analysis = runner.invoke(
        app,
        [
            "analyze",
            session_id,
            "--db",
            str(database_path),
            "--format",
            "json",
            "--no-artifact",
        ],
    )
    terminal_summary = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--agent", "claude"],
    )
    json_summary = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--format", "json"],
    )

    assert sessions_result.exit_code == 0
    assert "claude" in sessions_result.stdout
    assert str(fixture_path) in sessions_result.stdout
    assert "Messages" in sessions_result.stdout
    assert "Commands" in sessions_result.stdout
    assert terminal_analysis.exit_code == 0
    assert "Session analysis" in terminal_analysis.stdout
    assert "friction_score" in terminal_analysis.stdout
    assert json_analysis.exit_code == 0
    analysis_payload = cast("dict[str, Any]", json.loads(json_analysis.stdout))
    assert analysis_payload["session"]["agent_name"] == "claude"
    assert analysis_payload["session"]["session_id"] == session_id
    assert terminal_summary.exit_code == 0
    assert "Aggregate summary" in terminal_summary.stdout
    assert "claude" in terminal_summary.stdout
    assert json_summary.exit_code == 0
    aggregate_payload = cast("dict[str, Any]", json.loads(json_summary.stdout))
    assert aggregate_payload["totals"] == {
        "sessions": 1,
        "analyzed_sessions": 1,
        "unanalyzed_sessions": 0,
    }
    assert aggregate_payload["agents"] == [
        {"agent": "claude", "sessions": 1, "analyzed_sessions": 1}
    ]


def test_analyze_json_format_still_writes_default_artifact(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0
    session_id = DuckDBStore(database_path).list_session_summaries()[0].session_id

    result = runner.invoke(
        app,
        ["analyze", session_id, "--db", str(database_path), "--format", "json"],
    )

    assert result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert payload["session"]["session_id"] == session_id
    assert "session_features" in payload
    assert "classifications" in payload
    assert "friction_score" in payload["summary_metrics"]
    assert (tmp_path / "artifacts" / f"{session_id}-analysis.json").exists()


def test_analyze_no_artifact_skips_default_artifact(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    fixture_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
    ingest_result = runner.invoke(
        app,
        [
            "ingest",
            "--agent",
            "codex",
            "--source",
            str(fixture_path),
            "--db",
            str(database_path),
        ],
    )
    assert ingest_result.exit_code == 0
    session_id = DuckDBStore(database_path).list_session_summaries()[0].session_id

    result = runner.invoke(
        app,
        ["analyze", session_id, "--db", str(database_path), "--no-artifact"],
    )

    assert result.exit_code == 0
    assert not (tmp_path / "artifacts" / f"{session_id}-analysis.json").exists()


def test_analyze_artifact_failure_does_not_expose_path(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    insert_batch_session(
        store,
        session_id="session-artifact-failure",
        project_path="/work/project",
        started_at=None,
    )
    private_artifact_path = tmp_path / "private-TOP_SECRET" / "result.json"
    private_artifact_path.parent.write_text("not a directory", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "analyze",
            "session-artifact-failure",
            "--db",
            str(database_path),
            "--artifact",
            str(private_artifact_path),
        ],
    )

    assert result.exit_code == 1
    assert "Could not write analysis artifact" in result.stdout
    assert "TOP_SECRET" not in result.stdout
    assert str(private_artifact_path) not in result.stdout


def test_analyze_all_selects_stale_and_missing_then_skips_current(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    insert_batch_session(
        store,
        session_id="session-current",
        project_path="/work/project",
        started_at=datetime(2026, 1, 1, 8, 0),
    )
    insert_batch_session(
        store,
        session_id="session-stale",
        project_path="/work/project/subdir",
        started_at=datetime(2026, 1, 2, 8, 0),
    )
    insert_batch_session(
        store,
        session_id="session-missing",
        project_path="/work/project",
        started_at=None,
    )
    insert_batch_session(
        store,
        session_id="session-other-agent",
        project_path="/work/project",
        started_at=datetime(2026, 1, 3, 8, 0),
        agent_name=AgentName.PI,
    )
    add_empty_analysis(store, "session-current", ANALYZER_VERSION)
    add_empty_analysis(store, "session-stale", "phase5")

    result = runner.invoke(
        app,
        [
            "analyze",
            "--all",
            "--db",
            str(database_path),
            "--project",
            "/work/project",
            "--agent",
            "codex",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert payload["analyzer_version"] == ANALYZER_VERSION
    assert payload["filters"] == {"agent": "codex", "project": "/work/project"}
    assert payload["counts"] == {
        "matching": 3,
        "selected": 2,
        "succeeded": 2,
        "skipped": 1,
        "failed": 0,
    }
    assert payload["succeeded_session_ids"] == ["session-stale", "session-missing"]
    assert payload["skipped_session_ids"] == ["session-current"]
    assert payload["failures"] == []
    assert not (tmp_path / "artifacts").exists()

    rerun = runner.invoke(
        app,
        ["analyze", "--all", "--db", str(database_path), "--format", "json"],
    )

    assert rerun.exit_code == 0
    rerun_payload = cast("dict[str, Any]", json.loads(rerun.stdout))
    assert rerun_payload["counts"] == {
        "matching": 4,
        "selected": 1,
        "succeeded": 1,
        "skipped": 3,
        "failed": 0,
    }
    assert rerun_payload["succeeded_session_ids"] == ["session-other-agent"]


def test_analyze_all_force_writes_artifacts_for_current_sessions(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    insert_batch_session(
        store,
        session_id="session-current",
        project_path="/work/project",
        started_at=datetime(2026, 1, 1, 8, 0),
    )
    add_empty_analysis(store, "session-current", ANALYZER_VERSION)

    result = runner.invoke(
        app,
        [
            "analyze",
            "--all",
            "--db",
            str(database_path),
            "--force",
            "--write-artifacts",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert payload["force"] is True
    assert payload["write_artifacts"] is True
    assert payload["counts"] == {
        "matching": 1,
        "selected": 1,
        "succeeded": 1,
        "skipped": 0,
        "failed": 0,
    }
    assert (tmp_path / "artifacts" / "session-current-analysis.json").exists()

    terminal_result = runner.invoke(
        app,
        ["analyze", "--all", "--db", str(database_path)],
    )

    assert terminal_result.exit_code == 0
    assert "Batch analysis" in terminal_result.stdout
    assert "session-current" in terminal_result.stdout
    assert "skipped" in terminal_result.stdout


def test_analyze_all_continues_after_safe_per_session_failure(tmp_path, monkeypatch) -> None:
    from session_doctor import batch_analysis

    database_path = tmp_path / "session-doctor.duckdb"
    store = DuckDBStore(database_path)
    for index, session_id in enumerate(("session-a", "session-b", "session-c"), start=1):
        insert_batch_session(
            store,
            session_id=session_id,
            project_path="/work/project",
            started_at=datetime(2026, 1, index, 8, 0),
        )
    original_analyze_session = batch_analysis.analyze_session

    def fail_middle_session(*args, **kwargs):
        if args[1] == "session-b":
            raise SessionAnalysisError("TOP_SECRET /private/source.jsonl")
        return original_analyze_session(*args, **kwargs)

    monkeypatch.setattr(batch_analysis, "analyze_session", fail_middle_session)

    result = runner.invoke(
        app,
        ["analyze", "--all", "--db", str(database_path), "--format", "json"],
    )

    assert result.exit_code == 1
    assert "TOP_SECRET" not in result.stdout
    assert "/private/source.jsonl" not in result.stdout
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert payload["counts"] == {
        "matching": 3,
        "selected": 3,
        "succeeded": 2,
        "skipped": 0,
        "failed": 1,
    }
    assert payload["succeeded_session_ids"] == ["session-a", "session-c"]
    assert payload["failures"] == [
        {
            "session_id": "session-b",
            "code": "analysis_failed",
            "message": "Session analysis failed",
        }
    ]


@pytest.mark.parametrize(
    ("arguments", "expected_message"),
    [
        ([], "Choose exactly one"),
        (["session-a", "--all"], "Choose exactly one"),
        (["--all", "--artifact", "out.json"], "rejects --artifact"),
        (["--all", "--no-artifact"], "rejects --artifact"),
        (["session-a", "--write-artifacts"], "Single-session mode rejects"),
        (["session-a", "--force"], "Single-session mode rejects"),
        (["session-a", "--project", "/work"], "Single-session mode rejects"),
    ],
)
def test_analyze_rejects_conflicting_modes(tmp_path, arguments, expected_message) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    DuckDBStore(database_path).initialize()

    result = runner.invoke(app, ["analyze", *arguments, "--db", str(database_path)])

    assert result.exit_code == 2
    assert expected_message in result.stdout


def test_summary_rejects_missing_database(tmp_path) -> None:
    result = runner.invoke(app, ["summary", "--db", str(tmp_path / "missing.duckdb")])

    assert result.exit_code == 1
    assert "Database does not exist" in result.stdout


def test_summary_empty_initialized_database_prints_zero_totals(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    init_result = runner.invoke(app, ["db", "init", "--db", str(database_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(app, ["summary", "--db", str(database_path)])

    assert result.exit_code == 0
    assert "Aggregate summary" in result.stdout
    assert "Sessions" in result.stdout
    assert "0" in result.stdout


def test_summary_json_counts_analyzed_codex_and_pi_sessions(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    codex_path = CODEX_FIXTURE_DIR / "repeated-failure-session.jsonl"
    pi_path = PI_FIXTURE_DIR / "repeated-failure-session.jsonl"

    for agent, fixture_path in (("codex", codex_path), ("pi", pi_path)):
        ingest_result = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                agent,
                "--source",
                str(fixture_path),
                "--db",
                str(database_path),
            ],
        )
        assert ingest_result.exit_code == 0

    session_ids = [
        summary.session_id for summary in DuckDBStore(database_path).list_session_summaries()
    ]
    for session_id in session_ids:
        analyze_result = runner.invoke(
            app,
            ["analyze", session_id, "--db", str(database_path), "--no-artifact"],
        )
        assert analyze_result.exit_code == 0

    result = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--format", "json"],
    )

    assert result.exit_code == 0
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert payload["totals"] == {
        "sessions": 2,
        "analyzed_sessions": 2,
        "unanalyzed_sessions": 0,
    }
    assert {row["agent"] for row in payload["agents"]} == {"codex", "pi"}
    assert payload["classifications"]
    assert payload["recent_risk_sessions"]
    for risk_row in payload["recent_risk_sessions"]:
        assert {
            "friction_score",
            "stuckness_score",
            "prompt_clarity_risk",
            "agent_fit_risk",
            "project_complexity_signal",
            "max_risk_score",
        }.issubset(risk_row)
        assert all(
            value is None or value == round(value, 3)
            for key, value in risk_row.items()
            if key.endswith(("_score", "_risk", "_signal"))
        )
    assert payload["failed_commands"]
    assert payload["recommendations"]


def test_summary_filters_by_agent_and_project(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    codex_path = CODEX_FIXTURE_DIR / "basic-session.jsonl"
    pi_path = PI_FIXTURE_DIR / "basic-session.jsonl"

    for agent, fixture_path in (("codex", codex_path), ("pi", pi_path)):
        result = runner.invoke(
            app,
            [
                "ingest",
                "--agent",
                agent,
                "--source",
                str(fixture_path),
                "--db",
                str(database_path),
            ],
        )
        assert result.exit_code == 0

    agent_result = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--agent", "pi", "--format", "json"],
    )
    project_result = runner.invoke(
        app,
        [
            "summary",
            "--db",
            str(database_path),
            "--project",
            "/tmp/../tmp/session-doctor",
            "--format",
            "json",
        ],
    )
    missing_project_result = runner.invoke(
        app,
        [
            "summary",
            "--db",
            str(database_path),
            "--project",
            "/tmp/not-session-doctor",
            "--format",
            "json",
        ],
    )

    assert agent_result.exit_code == 0
    assert project_result.exit_code == 0
    assert missing_project_result.exit_code == 0
    agent_payload = cast("dict[str, Any]", json.loads(agent_result.stdout))
    project_payload = cast("dict[str, Any]", json.loads(project_result.stdout))
    missing_project_payload = cast("dict[str, Any]", json.loads(missing_project_result.stdout))
    assert agent_payload["totals"]["sessions"] == 1
    assert agent_payload["agents"] == [{"agent": "pi", "sessions": 1, "analyzed_sessions": 0}]
    assert project_payload["totals"]["sessions"] == 2
    assert missing_project_payload["totals"]["sessions"] == 0


def test_summary_rejects_invalid_options(tmp_path) -> None:
    database_path = tmp_path / "session-doctor.duckdb"
    init_result = runner.invoke(app, ["db", "init", "--db", str(database_path)])
    assert init_result.exit_code == 0

    invalid_format = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--format", "yaml"],
    )
    invalid_agent = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--agent", "nonsense"],
    )
    unknown_agent = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--agent", "unknown"],
    )
    invalid_limit = runner.invoke(
        app,
        ["summary", "--db", str(database_path), "--limit", "0"],
    )

    assert invalid_format.exit_code == 2
    assert "Invalid --format" in invalid_format.stdout
    assert invalid_agent.exit_code == 2
    assert "Unsupported --agent" in invalid_agent.stdout
    assert unknown_agent.exit_code == 2
    assert "Unsupported --agent" in unknown_agent.stdout
    assert invalid_limit.exit_code == 2
    assert "Invalid --limit" in invalid_limit.stdout


def payload_feature(
    payload: dict[str, Any],
    collection_name: str,
    feature_name: str,
) -> dict[str, Any]:
    collection = payload[collection_name]
    assert isinstance(collection, list)
    for raw_item in collection:
        item = cast("dict[str, Any]", raw_item)
        if item.get("feature_name") == feature_name:
            return item
    raise AssertionError(f"Missing {feature_name} in {collection_name}")


def insert_batch_session(
    store: DuckDBStore,
    *,
    session_id: str,
    project_path: str,
    started_at: datetime | None,
    agent_name: AgentName = AgentName.CODEX,
) -> None:
    source = SessionSource(
        source_id=f"source-{session_id}",
        agent_name=agent_name,
        source_path=f"/tmp/{session_id}.jsonl",
    )
    store.insert_untracked_parsed_bundle(
        source,
        ParsedSessionBundle(
            session=Session(
                session_id=session_id,
                source_id=source.source_id,
                agent_name=agent_name,
                project_path=project_path,
                started_at=started_at,
            )
        ),
    )


def add_empty_analysis(store: DuckDBStore, session_id: str, analyzer_version: str) -> None:
    store.replace_analysis_rows(
        AnalysisRun(
            analysis_run_id=f"analysis-{session_id}-{analyzer_version}",
            session_id=session_id,
            analyzer_version=analyzer_version,
            started_at=datetime(2026, 2, 1, 8, 0),
            completed_at=datetime(2026, 2, 1, 8, 1),
        ),
        [],
        [],
        [],
    )


def payload_feature_evidence(
    payload: dict[str, Any],
    collection_name: str,
    feature_name: str,
) -> dict[str, Any]:
    evidence = payload_feature(payload, collection_name, feature_name)["evidence"]
    assert isinstance(evidence, dict)
    return cast("dict[str, Any]", evidence)
