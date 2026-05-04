from __future__ import annotations

from typer.testing import CliRunner

from session_doctor import __version__
from session_doctor.cli import app

runner = CliRunner()


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


def test_adapters_list_without_scan() -> None:
    result = runner.invoke(app, ["adapters", "list"])

    assert result.exit_code == 0
    assert "Codex" in result.stdout
    assert "Claude Code" in result.stdout
    assert "Pi" in result.stdout
