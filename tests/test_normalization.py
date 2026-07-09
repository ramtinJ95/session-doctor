from __future__ import annotations

from session_doctor.normalization import canonical_command_identity, canonical_file_identity


def test_command_identity_unwraps_only_recognized_shell_payloads() -> None:
    plain = canonical_command_identity("pytest tests/test_cli.py -q")

    for wrapped in (
        "/bin/zsh -lc 'pytest tests/test_cli.py -q'",
        "/usr/bin/bash -lc 'pytest tests/test_cli.py -q'",
        "bash -c 'pytest tests/test_cli.py -q'",
        "sh -lc 'pytest tests/test_cli.py -q'",
    ):
        identity = canonical_command_identity(wrapped)
        assert identity.identity_hash == plain.identity_hash
        assert identity.display == plain.display
        assert identity.normalization.startswith("shell_wrapper:")


def test_command_identity_keeps_near_miss_wrappers_separate() -> None:
    plain = canonical_command_identity("pytest -q")

    for near_miss in (
        "env bash -c 'pytest -q'",
        "bash -l -c 'pytest -q'",
        "bash -lc 'pytest -q' extra",
        "fish -c 'pytest -q'",
        "bash -x 'pytest -q'",
        "bash -lc 'pytest -q",
        "./bash -c 'pytest -q'",
        "/tmp/bash -lc 'pytest -q'",
        "/usr/local/bin/zsh -lc 'pytest -q'",
    ):
        assert canonical_command_identity(near_miss).identity_hash != plain.identity_hash


def test_command_identity_redacts_display_after_deriving_identity() -> None:
    identity = canonical_command_identity("TOKEN=supersecret pytest -q")
    redacted_input = canonical_command_identity("TOKEN=<redacted> pytest -q")

    assert identity.display == "TOKEN=<redacted> pytest -q"
    assert identity.identity_hash != redacted_input.identity_hash


def test_file_identity_groups_relative_and_absolute_paths_under_project() -> None:
    relative = canonical_file_identity(
        "tests/./unit/../test_cli.py",
        cwd="/tmp/session-doctor",
        project_path="/tmp/session-doctor",
    )
    absolute = canonical_file_identity(
        "/tmp/session-doctor/tests/test_cli.py",
        cwd=None,
        project_path="/tmp/session-doctor",
    )

    assert relative.canonical_path == absolute.canonical_path
    assert relative.project_relative_path == absolute.project_relative_path == "tests/test_cli.py"
    assert relative.resolution == "cwd"
    assert absolute.resolution == "absolute"


def test_file_identity_preserves_outside_project_and_missing_base_states() -> None:
    outside = canonical_file_identity(
        "../other/file.py",
        cwd="/tmp/session-doctor",
        project_path="/tmp/session-doctor",
    )
    unresolved = canonical_file_identity(
        "src/../README.md",
        cwd=None,
        project_path=None,
    )

    assert outside.canonical_path == "/tmp/other/file.py"
    assert outside.project_relative_path is None
    assert unresolved.normalized_path == "README.md"
    assert unresolved.canonical_path is None
    assert unresolved.project_relative_path is None
    assert unresolved.resolution == "unresolved"
