from __future__ import annotations

import json
from inspect import signature
from pathlib import Path
from shutil import copyfile
from typing import Any, cast

import duckdb
import pytest
from typer.testing import CliRunner

from session_doctor import __version__
from session_doctor.adapters import BaseAdapter, ParsedSessionBundle, SourceReadError
from session_doctor.cli import app
from session_doctor.ids import source_id_for_path
from session_doctor.schemas import AgentName, Session, SessionSource
from session_doctor.store import SCHEMA_VERSION, DuckDBStore

runner = CliRunner()
CODEX_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"
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

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
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
        assert "expected 3" in result.stdout
        assert "Delete it and recreate it" in result.stdout
        assert "BinderException" not in result.stdout


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
        ["ingest", "--agent", "claude", "--db", str(tmp_path / "session-doctor.duckdb")],
    )

    assert result.exit_code == 2
    assert "--agent claude is discovered but parsing is not implemented" in result.stdout


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

    def fail_parse(source: SessionSource) -> ParsedSessionBundle:
        raise RuntimeError("synthetic parser bug")

    monkeypatch.setattr(adapter, "parse_source", fail_parse)
    monkeypatch.setattr("session_doctor.cli.adapter_for_ingest", lambda agent: adapter)

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


def payload_feature_evidence(
    payload: dict[str, Any],
    collection_name: str,
    feature_name: str,
) -> dict[str, Any]:
    evidence = payload_feature(payload, collection_name, feature_name)["evidence"]
    assert isinstance(evidence, dict)
    return cast("dict[str, Any]", evidence)
