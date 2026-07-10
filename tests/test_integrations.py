from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from session_doctor import __version__
from session_doctor.cli import app
from session_doctor.integration_assets import IntegrationAssetError

runner = CliRunner()


def test_integrations_path_emits_exact_existing_skill_directory() -> None:
    result = runner.invoke(app, ["integrations", "path"])

    assert result.exit_code == 0
    assert result.stdout.count("\n") == 1
    skill_directory = Path(result.stdout.strip())
    assert skill_directory.is_absolute()
    assert {path.name for path in skill_directory.iterdir()} == {"SKILL.md"}


def test_integrations_path_fails_closed_when_asset_is_missing(monkeypatch) -> None:
    def missing_skill() -> Path:
        raise IntegrationAssetError(
            "Bundled session-doctor skill is unavailable. Reinstall session-doctor."
        )

    monkeypatch.setattr("session_doctor.cli.session_doctor_skill_directory", missing_skill)

    result = runner.invoke(app, ["integrations", "path"])

    assert result.exit_code == 1
    assert result.stdout.strip() == (
        "Bundled session-doctor skill is unavailable. Reinstall session-doctor."
    )


def test_skill_frontmatter_and_version_contract() -> None:
    skill_text = skill_markdown()
    frontmatter = skill_text.split("---", 2)[1]

    assert "name: session-doctor" in frontmatter
    assert "description:" in frontmatter
    assert "license: MIT" in frontmatter
    assert f"compatibility: Requires session-doctor CLI version {__version__}." in frontmatter
    assert f'session-doctor-version: "{__version__}"' in frontmatter
    assert "allowed-tools:" not in frontmatter
    assert "disable-model-invocation:" not in frontmatter


def test_skill_covers_public_cli_and_rejects_private_shortcuts() -> None:
    skill_text = skill_markdown()
    command_markers = (
        "session-doctor version",
        "session-doctor doctor",
        "session-doctor adapters list",
        "session-doctor db init",
        "session-doctor db info",
        "session-doctor ingest",
        "session-doctor sessions list",
        "session-doctor analyze",
        "session-doctor summary",
        "session-doctor trends",
        "session-doctor projects list",
        "session-doctor report",
        "session-doctor graph",
        "session-doctor integrations path",
        "session-doctor --install-completion",
        "session-doctor --show-completion",
    )

    assert all(marker in skill_text for marker in command_markers)
    assert "Never open or query the DuckDB database directly" in skill_text
    assert "Never read native session transcripts" in skill_text
    assert "unavailable `explain`, `export`, MCP" in skill_text


def test_skill_has_write_disclosure_and_interpretation_guards() -> None:
    skill_text = skill_markdown()

    assert "Write Confirmation Protocol" in skill_text
    assert "A request to diagnose, inspect, fix, or review is not write authorization" in skill_text
    assert "Message-Text Confirmation" in skill_text
    assert "Write confirmation does not authorize `--show-text`" in skill_text
    assert "Never turn correlation into causality" in skill_text
    assert "cite stable report or graph evidence IDs" in skill_text
    assert "If stale/missing analysis is returned" in skill_text


def test_integrations_path_does_not_create_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())

    result = runner.invoke(app, ["integrations", "path"])

    assert result.exit_code == 0
    assert tuple(tmp_path.iterdir()) == before


def skill_markdown() -> str:
    result = runner.invoke(app, ["integrations", "path"])
    assert result.exit_code == 0
    return (Path(result.stdout.strip()) / "SKILL.md").read_text()
