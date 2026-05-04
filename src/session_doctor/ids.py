from __future__ import annotations

import hashlib
from pathlib import Path

from .schemas.common import AgentName


def stable_id(*parts: object) -> str:
    normalized_parts = ["" if part is None else str(part) for part in parts]
    payload = "\x1f".join(normalized_parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_id_for_path(agent_name: AgentName, path: Path | str) -> str:
    return stable_id("source", agent_name.value, Path(path).expanduser())

