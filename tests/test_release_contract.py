from __future__ import annotations

import tomllib
from pathlib import Path

from session_doctor import __version__

ROOT = Path(__file__).parent.parent


def test_package_version_has_one_source() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert pyproject["project"]["dynamic"] == ["version"]
    assert "version" not in pyproject["project"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == ("src/session_doctor/__init__.py")
    assert __version__ == "0.1.0"


def test_release_metadata_and_license_are_truthful() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = pyproject["project"]
    license_text = (ROOT / "LICENSE").read_text()

    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "ramtinJ95"}]
    assert project["urls"]["Repository"] == "https://github.com/ramtinJ95/session-doctor"
    assert "Copyright (c) 2026 ramtinJ95" in license_text
    assert "MIT License" in license_text


def test_changelog_records_dogfood_scope_and_compatibility() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert "## 0.1.0 - 2026-07-10" in changelog
    assert "First dogfood baseline" in changelog
    assert "0.x databases and generated artifacts may need rebuilding" in changelog
    assert "no OpenCode adapter, MCP/query server, export command, CI, PyPI package" in changelog


def test_dogfood_issue_template_requires_privacy_safe_evidence() -> None:
    template = (ROOT / ".github/ISSUE_TEMPLATE/dogfood.yml").read_text()
    required_markers = (
        "session-doctor version",
        "Operating system and Python version only",
        "Adapter",
        "Public command",
        "Analysis state",
        "Privacy-safe structural evidence",
        "Expected behavior",
        "Actual behavior",
        "Minimal synthetic reproduction",
        "Privacy confirmation",
        "Do not attach DuckDB files",
        "Do not paste transcripts",
        "native IDs",
        "hashes/fingerprints",
        "tool/command output",
        "diffs",
        "file content",
    )

    assert all(marker in template for marker in required_markers)
    assert template.count("required: true") >= 8
