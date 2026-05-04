from __future__ import annotations

import os
import sys
from pathlib import Path

from .constants import APPLICATION_NAME


def default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / APPLICATION_NAME


def default_database_path() -> Path:
    configured_path = os.environ.get("SESSION_DOCTOR_DB")
    if configured_path:
        return Path(configured_path).expanduser()
    return default_data_dir() / "session-doctor.duckdb"


def supports_current_python() -> bool:
    return sys.version_info >= (3, 12)
