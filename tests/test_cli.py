from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfile

from typer.testing import CliRunner

from session_doctor import __version__
from session_doctor.cli import app
from session_doctor.store import DuckDBStore

runner = CliRunner()
CODEX_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex"
PI_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pi"


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
    artifact_path = tmp_path / "artifacts" / f"{session_id}-analysis.json"
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text())
    assert payload["session"]["session_id"] == session_id
    assert "failed_command_ratio" in payload["summary_metrics"]
    labels = {classification["label"] for classification in payload["classifications"]}
    assert {"user_stuck", "tooling_blocked", "agent_looping"}.issubset(labels)
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
    artifact_path = tmp_path / "artifacts" / f"{session_id}-analysis.json"
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text())
    assert payload["session"]["session_id"] == session_id
    assert payload["session"]["agent_name"] == "pi"
    assert "failed_command_ratio" in payload["summary_metrics"]
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
    payload = json.loads(result.stdout)
    assert payload["session"]["session_id"] == session_id
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
