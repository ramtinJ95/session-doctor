from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoreInfo:
    database_path: Path
    exists: bool
    schema_version: int | None
    tables: tuple[str, ...]


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    agent_name: str
    started_at: str | None
    ended_at: str | None
    cwd: str | None
    project_path: str | None
    source_path: str | None
    message_count: int
    response_item_message_count: int
    event_msg_fallback_count: int
    command_count: int
    warning_count: int
