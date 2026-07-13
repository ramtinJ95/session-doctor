from __future__ import annotations

import json
from datetime import datetime
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
from session_doctor.cli import app
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, Session, SessionSource
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
    assert root_snapshot.snapshot_bundle_id is not None
    child_member = next(
        member
        for member in store.load_bundle_members(root_snapshot.snapshot_bundle_id)
        if member.source_path == str(child_path)
    )
    assert root_snapshot.capture_status == "incomplete"
    assert child_member.source_bytes == b"\xff"


def test_malformed_claude_metadata_keeps_root_bundle_incomplete(tmp_path) -> None:
    source_root = tmp_path / "topology"
    copytree(CLAUDE_TOPOLOGY_FIXTURE_DIR, source_root)
    metadata_path = source_root / "project/session-root/subagents/agent-a.meta.json"
    metadata_path.write_bytes(b"\xff")
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


def test_primary_change_during_member_capture_marks_bundle_skewed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "session.jsonl"
    copyfile(CLAUDE_FIXTURE_DIR / "basic-session.jsonl", source_path)
    original_members = ClaudeCodeAdapter.bundle_member_sources

    def mutate_after_discovery(self, source, source_bytes):
        members = original_members(self, source, source_bytes)
        Path(source.source_path).write_bytes(source_bytes + b"\n")
        return members

    monkeypatch.setattr(ClaudeCodeAdapter, "bundle_member_sources", mutate_after_discovery)
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

    assert result.exit_code == 0
    snapshot = DuckDBStore(database_path).list_snapshots()[0]
    assert snapshot.capture_status == "skewed"
    assert snapshot.lifecycle_state == "snapshot_incomplete"


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


def payload_feature_evidence(
    payload: dict[str, Any],
    collection_name: str,
    feature_name: str,
) -> dict[str, Any]:
    evidence = payload_feature(payload, collection_name, feature_name)["evidence"]
    assert isinstance(evidence, dict)
    return cast("dict[str, Any]", evidence)
