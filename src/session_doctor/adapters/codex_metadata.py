from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.schemas import AgentName, Session, SessionSource

from .common import dict_value, parse_timestamp, string_value


class CodexSessionMetadata:
    def __init__(self, session: Session, session_id: str) -> None:
        self.session = session
        self.session_id = session_id


def extract_session_metadata(
    source: SessionSource,
    source_path: Path,
    records: list[tuple[int, dict[str, Any]]],
) -> CodexSessionMetadata:
    session_meta: dict[str, Any] = {}
    turn_contexts: list[dict[str, Any]] = []
    timestamps: list[datetime] = []
    has_compaction = False

    for _, record in records:
        record_type = string_value(record.get("type"))
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp:
            timestamps.append(timestamp)
        payload = dict_value(record.get("payload"))
        if record_type == "session_meta":
            session_meta = payload
        elif record_type == "turn_context":
            turn_contexts.append(payload)
        elif record_type == "compacted":
            has_compaction = True

    latest_turn = turn_contexts[-1] if turn_contexts else {}
    native_session_id = string_value(session_meta.get("id")) or session_id_from_filename(
        source_path
    )
    session_id = stable_id("session", AgentName.CODEX.value, source.source_path, native_session_id)
    cwd = string_value(latest_turn.get("cwd")) or string_value(session_meta.get("cwd"))
    model = string_value(latest_turn.get("model")) or string_value(session_meta.get("model"))
    model_provider = string_value(latest_turn.get("model_provider")) or string_value(
        session_meta.get("model_provider")
    )
    model_changes = [
        {
            "provider": string_value(turn.get("model_provider"))
            or string_value(session_meta.get("model_provider")),
            "model": string_value(turn.get("model")),
        }
        for turn in turn_contexts
        if string_value(turn.get("model")) is not None
    ]
    if not model_changes and model is not None:
        model_changes.append(
            {
                "provider": string_value(session_meta.get("model_provider")),
                "model": model,
            }
        )

    session = Session(
        session_id=session_id,
        source_id=source.source_id,
        agent_name=AgentName.CODEX,
        native_session_id=native_session_id,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        cwd=cwd,
        project_path=cwd,
        agent_version=string_value(session_meta.get("cli_version")),
        model_provider=model_provider,
        model=model,
        metadata={
            "source_path": source.source_path,
            "originator": string_value(session_meta.get("originator")),
            "source": string_value(session_meta.get("source")),
            "has_compaction": has_compaction,
            "model_changes": model_changes,
        },
    )
    return CodexSessionMetadata(session=session, session_id=session_id)


def session_id_from_filename(path: Path) -> str | None:
    stem = path.stem
    if "-" not in stem:
        return stem or None
    return stem.rsplit("-", maxsplit=1)[-1]
