from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .constants import APPLICATION_NAME


@dataclass(frozen=True)
class AdapterRoot:
    agent_name: str
    display_name: str
    path: Path


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / APPLICATION_NAME


def default_database_path() -> Path:
    configured_path = os.environ.get("SESSION_DOCTOR_DB")
    if configured_path:
        return Path(configured_path).expanduser()
    return default_data_dir() / "session-doctor.duckdb"


def default_adapter_roots() -> tuple[AdapterRoot, ...]:
    home = Path.home()
    return (
        AdapterRoot("codex", "Codex", home / ".codex" / "sessions"),
        AdapterRoot("claude", "Claude Code", home / ".claude" / "projects"),
        AdapterRoot("pi", "Pi", home / ".pi" / "agent" / "sessions"),
    )


def supports_current_python() -> bool:
    return sys.version_info >= (3, 12)

