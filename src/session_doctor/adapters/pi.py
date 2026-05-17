from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from session_doctor.ids import source_id_for_path, stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import (
    AgentName,
    Message,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    SessionSource,
    SourceKind,
)

from .base import BaseAdapter, ParsedSessionBundle

PI_METADATA_ONLY_TYPES = {
    "branch_summary",
    "compaction",
    "custom",
    "custom_message",
    "label",
    "model_change",
    "session_info",
    "thinking_level_change",
}


class PiAdapter(BaseAdapter):
    name = AgentName.PI
    display_name = "Pi"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".pi" / "agent" / "sessions",)

    def discover(self, root: Path | None = None) -> list[SessionSource]:
        discovery_root = self.root_for_discovery(root)
        if not discovery_root.exists():
            return []

        return [
            SessionSource(
                source_id=source_id_for_path(self.name, path),
                agent_name=self.name,
                source_path=str(path),
                source_kind=SourceKind.ROOT_SESSION,
                metadata={"relative_path": str(path.relative_to(discovery_root))},
            )
            for path in sorted(discovery_root.rglob("*.jsonl"))
            if path.is_file()
        ]

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        valid_records, malformed_warnings = read_pi_jsonl(source, source_path)
        session_metadata = extract_session_metadata(source, source_path, valid_records)
        bundle = ParsedSessionBundle(
            session=session_metadata.session,
            parse_warnings=malformed_warnings,
        )
        metadata_only_counts: dict[str, int] = {}

        for record_index, record in valid_records:
            record_type = string_value(record.get("type"))
            event = raw_event_for_record(source, session_metadata.session_id, record_index, record)
            bundle.raw_events.append(event)

            if record_type == "message":
                message_payload = dict_value(record.get("message"))
                role = normalize_pi_role(string_value(message_payload.get("role")))
                if role is NormalizedRole.UNKNOWN:
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "unsupported_message_role",
                            "Unsupported Pi message role",
                            {"role": string_value(message_payload.get("role"))},
                        )
                    )
                    continue
                bundle.messages.append(
                    message_from_record(session_metadata.session_id, event, record)
                )
            elif record_type == "session":
                continue
            elif record_type in PI_METADATA_ONLY_TYPES:
                increment_count(metadata_only_counts, record_type or "missing")
            else:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unsupported_record_type",
                        f"Unsupported Pi record type: {record_type}",
                        {"record_type": record_type},
                    )
                )

        if bundle.session:
            bundle.session.metadata["pi_metadata_only_counts"] = metadata_only_counts

        return bundle


class PiSessionMetadata:
    def __init__(self, session: Session, session_id: str) -> None:
        self.session = session
        self.session_id = session_id


def read_pi_jsonl(
    source: SessionSource,
    source_path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], list[ParseWarning]]:
    records: list[tuple[int, dict[str, Any]]] = []
    warnings: list[ParseWarning] = []
    try:
        with source_path.open(encoding="utf-8") as file:
            for record_index, line in enumerate(file):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "malformed_json",
                            f"Malformed JSONL record: {exc.msg}",
                            {"line": exc.lineno, "column": exc.colno},
                        )
                    )
                    continue
                if not isinstance(parsed, dict):
                    warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "non_object_record",
                            "Pi record is not a JSON object",
                            {"json_type": type(parsed).__name__},
                        )
                    )
                    continue
                records.append((record_index, parsed))
    except OSError as exc:
        warnings.append(
            ParseWarning(
                warning_id=stable_id("warning", source.source_id, "source_open_error"),
                source_id=source.source_id,
                message=f"Unable to read Pi source: {exc}",
                metadata={"source_path": str(source_path)},
            )
        )
    return records, warnings


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
    cwd = string_value(session_record.get("cwd")) or cwd_from_source_path(source_path)
    model = string_value(latest_model_change.get("modelId"))

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


def raw_event_for_record(
    source: SessionSource,
    session_id: str,
    record_index: int,
    record: dict[str, Any],
) -> RawEvent:
    message_payload = dict_value(record.get("message"))
    return RawEvent(
        event_id=stable_id("event", session_id, source.source_path, record_index),
        source_id=source.source_id,
        agent_name=AgentName.PI,
        record_index=record_index,
        native_event_type=string_value(record.get("type")),
        native_event_id=string_value(record.get("id")),
        native_parent_id=string_value(record.get("parentId")),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        payload_hash=hash_json(record),
        metadata={
            "payload_keys": sorted(record.keys()),
            "message_role": string_value(message_payload.get("role")),
        },
    )


def message_from_record(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> Message:
    message_payload = dict_value(record.get("message"))
    text, content_block_types = text_and_block_types(message_payload.get("content"))
    role = normalize_pi_role(string_value(message_payload.get("role")))
    timestamp = string_value(message_payload.get("timestamp")) or string_value(
        record.get("timestamp")
    )
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(record.get("id")),
        parent_message_id=string_value(record.get("parentId")),
        timestamp=parse_timestamp(timestamp),
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=content_block_types,
        metadata={
            "pi_message_role": string_value(message_payload.get("role")),
            "stop_reason": string_value(message_payload.get("stopReason")),
            "model": string_value(message_payload.get("model")),
            "provider": string_value(message_payload.get("provider")),
        },
    )


def text_and_block_types(content: object) -> tuple[str | None, list[str]]:
    if isinstance(content, str):
        return content, ["text"]
    if not isinstance(content, list):
        return None, []

    texts: list[str] = []
    block_types: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        typed_block = dict_value(block)
        block_type = string_value(typed_block.get("type"))
        if block_type:
            block_types.append(block_type)
        if block_type == "text":
            block_text = string_value(typed_block.get("text"))
            if block_text is not None:
                texts.append(block_text)

    return "\n".join(texts) if texts else None, block_types


def normalize_pi_role(role: str | None) -> NormalizedRole:
    if role == "user":
        return NormalizedRole.USER
    if role == "assistant":
        return NormalizedRole.ASSISTANT
    if role == "toolResult":
        return NormalizedRole.TOOL
    if role == "bashExecution":
        return NormalizedRole.TOOL
    return NormalizedRole.UNKNOWN


def warning_for_record(
    source: SessionSource,
    record_index: int,
    code: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ParseWarning:
    return ParseWarning(
        warning_id=stable_id("warning", source.source_id, record_index, code),
        source_id=source.source_id,
        record_index=record_index,
        message=message,
        metadata={"code": code, **(metadata or {})},
    )


def increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def hash_json(value: object) -> str:
    return hash_text(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def session_id_from_filename(path: Path) -> str | None:
    stem = path.stem
    if "_" not in stem:
        return stem or None
    return stem.rsplit("_", maxsplit=1)[-1]


def cwd_from_source_path(path: Path) -> str | None:
    parent_name = path.parent.name
    if not parent_name.startswith("--") or not parent_name.endswith("--"):
        return None
    candidate = parent_name.removeprefix("--").removesuffix("--").replace("-", "/")
    return f"/{candidate.strip('/')}" if candidate else None


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def dict_value(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
