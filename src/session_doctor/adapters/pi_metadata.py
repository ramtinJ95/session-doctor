from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.schemas import AgentName, Session, SessionSource

from .common import parse_timestamp, string_value


class PiSessionMetadata:
    def __init__(self, session: Session, session_id: str) -> None:
        self.session = session
        self.session_id = session_id


def extract_session_metadata(
    source: SessionSource,
    source_path: Path,
    records: list[tuple[int, dict[str, Any]]],
) -> PiSessionMetadata:
    session_record: dict[str, Any] = {}
    model_changes: list[dict[str, Any]] = []
    timestamps: list[datetime] = []

    for _, record in records:
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp:
            timestamps.append(timestamp)
        record_type = string_value(record.get("type"))
        if record_type == "session":
            session_record = record
        elif record_type == "model_change":
            model_changes.append(record)

    latest_model_change = model_changes[-1] if model_changes else {}
    native_session_id = string_value(session_record.get("id")) or session_id_from_filename(
        source_path
    )
    session_id = stable_id("session", AgentName.PI.value, source.source_path, native_session_id)
    cwd = string_value(session_record.get("cwd"))
    model = string_value(latest_model_change.get("modelId"))
    source_path_project_hint = project_hint_from_source_path(source_path)

    session = Session(
        session_id=session_id,
        source_id=source.source_id,
        agent_name=AgentName.PI,
        native_session_id=native_session_id,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        cwd=cwd,
        project_path=cwd,
        agent_version=string_value(session_record.get("version")),
        model_provider=string_value(latest_model_change.get("provider")),
        model=model,
        metadata={
            "source_path": source.source_path,
            "source_path_project_hint": source_path_project_hint,
            "model_changes": [
                {
                    "provider": string_value(record.get("provider")),
                    "model": string_value(record.get("modelId")),
                    "timestamp": string_value(record.get("timestamp")),
                }
                for record in model_changes
            ],
        },
    )
    return PiSessionMetadata(session=session, session_id=session_id)


def has_usable_session_record(records: list[tuple[int, dict[str, Any]]]) -> bool:
    return any(
        string_value(record.get("type")) == "session" and string_value(record.get("id")) is not None
        for _, record in records
    )


def session_id_from_filename(path: Path) -> str | None:
    stem = path.stem
    if "_" not in stem:
        return stem or None
    return stem.rsplit("_", maxsplit=1)[-1]


def project_hint_from_source_path(path: Path) -> str | None:
    parent_name = path.parent.name
    if not parent_name.startswith("--") or not parent_name.endswith("--"):
        return None
    candidate = parent_name.removeprefix("--").removesuffix("--").replace("-", "/")
    return f"/{candidate.strip('/')}" if candidate else None
