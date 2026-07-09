from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass

from .privacy import hash_text, redact_command_for_display

SUPPORTED_SHELL_EXECUTABLES: dict[str, str] = {
    shell_path: shell_name
    for shell_name in ("bash", "sh", "zsh")
    for shell_path in (shell_name, f"/bin/{shell_name}", f"/usr/bin/{shell_name}")
}
SUPPORTED_SHELL_FLAGS = frozenset({"-c", "-lc"})


@dataclass(frozen=True)
class CommandIdentity:
    identity_hash: str
    display: str
    normalization: str


@dataclass(frozen=True)
class FileIdentity:
    normalized_path: str
    canonical_path: str | None
    project_relative_path: str | None
    resolution: str


def canonical_command_identity(command: str) -> CommandIdentity:
    trimmed_command = command.strip()
    canonical_command = trimmed_command
    normalization = "trimmed" if trimmed_command != command else "unchanged"

    wrapper = recognized_shell_wrapper(trimmed_command)
    if wrapper is not None:
        shell_name, shell_flag, payload = wrapper
        canonical_command = payload.strip()
        normalization = f"shell_wrapper:{shell_name}:{shell_flag}"

    return CommandIdentity(
        identity_hash=hash_text(canonical_command),
        display=redact_command_for_display(canonical_command),
        normalization=normalization,
    )


def recognized_shell_wrapper(command: str) -> tuple[str, str, str] | None:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(parts) != 3:
        return None

    shell_name = SUPPORTED_SHELL_EXECUTABLES.get(parts[0])
    shell_flag = parts[1]
    if shell_name is None or shell_flag not in SUPPORTED_SHELL_FLAGS:
        return None
    return shell_name, shell_flag, parts[2]


def canonical_file_identity(
    path: str,
    *,
    cwd: str | None,
    project_path: str | None,
) -> FileIdentity:
    normalized_path = posixpath.normpath(path) if path else path
    if not normalized_path:
        return FileIdentity(normalized_path, None, None, "unresolved")

    normalized_project = absolute_normalized_path(project_path)
    if posixpath.isabs(normalized_path):
        canonical_path = normalized_path
        resolution = "absolute"
    else:
        normalized_cwd = absolute_normalized_path(cwd)
        base_path = normalized_cwd or normalized_project
        if base_path is None:
            return FileIdentity(normalized_path, None, None, "unresolved")
        canonical_path = posixpath.normpath(posixpath.join(base_path, normalized_path))
        resolution = "cwd" if normalized_cwd is not None else "project_path"

    project_relative_path = relative_to_project(canonical_path, normalized_project)
    return FileIdentity(
        normalized_path=normalized_path,
        canonical_path=canonical_path,
        project_relative_path=project_relative_path,
        resolution=resolution,
    )


def absolute_normalized_path(path: str | None) -> str | None:
    if path is None or not posixpath.isabs(path):
        return None
    return posixpath.normpath(path)


def relative_to_project(path: str, project_path: str | None) -> str | None:
    if project_path is None:
        return None
    try:
        if posixpath.commonpath((path, project_path)) != project_path:
            return None
    except ValueError:
        return None
    return posixpath.relpath(path, project_path)
