from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import venv
import zipfile
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
        "session-doctor doctor",
        "session-doctor adapters",
        "session-doctor db init",
        "session-doctor ingest",
        "session-doctor sessions list",
        "session-doctor analyze",
        "session-doctor integrations path",
    )

    assert all(marker in skill_text for marker in command_markers)
    assert "not read native transcripts or query DuckDB directly" in skill_text
    assert "no v1 fallback" in skill_text.lower()
    assert "summary\ntrends\nprojects list\nreport\ngraph" in skill_text


def test_skill_has_write_disclosure_and_interpretation_guards() -> None:
    skill_text = skill_markdown()

    assert "Never add" in skill_text
    assert "--overwrite" in skill_text
    assert "--force" in skill_text
    assert "does not contain v1 labels, risk scores" in skill_text
    assert "Preserve unknown, ambiguous, active, incomplete" in skill_text


def test_skill_classifies_html_as_an_explicit_replacing_write() -> None:
    skill_text = skill_markdown()

    assert "report\ngraph" in skill_text
    assert "must not be invoked or emulated" in skill_text
    assert "Do not fall back to old scores" in skill_text


def test_integrations_path_does_not_create_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())

    result = runner.invoke(app, ["integrations", "path"])

    assert result.exit_code == 0
    assert tuple(tmp_path.iterdir()) == before


def test_built_distributions_and_clean_wheel_install_include_skill(tmp_path) -> None:
    output_directory = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--out-dir", str(output_directory)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_path = next(output_directory.glob("*.whl"))
    sdist_path = next(output_directory.glob("*.tar.gz"))
    wheel_skill = "session_doctor/integrations/session-doctor/SKILL.md"
    with zipfile.ZipFile(wheel_path) as wheel:
        assert wheel_skill in wheel.namelist()
        metadata_path = next(name for name in wheel.namelist() if name.endswith("/METADATA"))
        metadata = wheel.read(metadata_path).decode()
        assert f"Version: {__version__}" in metadata
        assert "License-Expression: MIT" in metadata
        assert "Author: ramtinJ95" in metadata
        assert "Project-URL: Repository, https://github.com/ramtinJ95/session-doctor" in metadata
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in wheel.namelist())
    with tarfile.open(sdist_path) as sdist:
        assert any(name.endswith(f"/src/{wheel_skill}") for name in sdist.getnames())

    environment = tmp_path / "clean-environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess_environment = os.environ.copy()
    subprocess_environment.pop("PYTHONPATH", None)
    requirements_path = tmp_path / "locked-requirements.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--output-file",
            str(requirements_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
        env=subprocess_environment,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--requirement",
            str(requirements_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel_path)],
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )
    result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "from session_doctor.integration_assets import "
                "session_doctor_skill_directory; print(session_doctor_skill_directory())"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=subprocess_environment,
    )
    installed_skill = Path(result.stdout.strip())
    assert installed_skill.is_dir()
    assert (installed_skill / "SKILL.md").is_file()

    executable = environment / (
        "Scripts/session-doctor.exe" if sys.platform == "win32" else "bin/session-doctor"
    )
    database_path = tmp_path / "installed.duckdb"
    fixture_path = Path(__file__).parent / "fixtures" / "codex" / "repeated-failure-session.jsonl"

    def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(executable), *arguments],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=subprocess_environment,
        )

    run_cli(
        "ingest",
        "--agent",
        "codex",
        "--source",
        str(fixture_path),
        "--db",
        str(database_path),
    )
    session_id_result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import duckdb; print(duckdb.connect(" + repr(str(database_path)) + ")"
                ".execute('SELECT session_id FROM sessions').fetchone()[0])"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=subprocess_environment,
    )
    session_id = session_id_result.stdout.strip()
    analysis_result = run_cli(
        "analyze",
        session_id,
        "--db",
        str(database_path),
        "--format",
        "json",
    )
    analysis_payload = json.loads(analysis_result.stdout)
    assert analysis_payload["session_id"] == session_id
    assert analysis_payload["episodes"]
    assert not (tmp_path / "artifacts").exists()
    assert not tuple(tmp_path.glob("*.css"))
    assert not tuple(tmp_path.glob("*.js"))


def skill_markdown() -> str:
    result = runner.invoke(app, ["integrations", "path"])
    assert result.exit_code == 0
    return (Path(result.stdout.strip()) / "SKILL.md").read_text()
