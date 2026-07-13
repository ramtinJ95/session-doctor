from __future__ import annotations

from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text
from session_doctor.schemas import AgentName, ParseWarning, RawEvent, SessionSource

from .common import (
    JsonRecord,
    content_blocks,
    dict_value,
    hash_json,
    parse_timestamp,
    read_jsonl_records,
    string_value,
)


def read_claude_jsonl(
    source: SessionSource,
    source_path: Path,
    source_bytes: bytes | None = None,
) -> tuple[list[JsonRecord], list[ParseWarning]]:
    return read_jsonl_records(
        source,
        source_path,
        agent_display_name="Claude Code",
        source_bytes=source_bytes,
    )


def raw_event_for_record(
    source: SessionSource,
    session_id: str,
    record_index: int,
    record: dict[str, Any],
) -> RawEvent:
    message = dict_value(record.get("message"))
    block_types = [
        string_value(block.get("type")) or "unknown"
        for block in content_blocks(message.get("content"))
    ]
    metadata: dict[str, Any] = {
        "payload_keys": sorted(record.keys()),
        "message_role": string_value(message.get("role")),
        "content_block_types": block_types,
        "is_sidechain": record.get("isSidechain")
        if isinstance(record.get("isSidechain"), bool)
        else None,
    }
    local_command_output = string_value(record.get("content"))
    if (
        string_value(record.get("type")) == "system"
        and string_value(record.get("subtype")) == "local_command"
        and local_command_output is not None
    ):
        metadata["local_command_output_hash"] = hash_text(local_command_output)
        metadata["local_command_output_length"] = len(local_command_output)
    return RawEvent(
        event_id=stable_id("event", session_id, source.source_path, record_index),
        source_id=source.source_id,
        agent_name=AgentName.CLAUDE,
        record_index=record_index,
        native_event_type=string_value(record.get("type")),
        native_event_id=string_value(record.get("uuid")),
        native_parent_id=string_value(record.get("parentUuid")),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        payload_hash=hash_json(record),
        metadata=metadata,
    )
