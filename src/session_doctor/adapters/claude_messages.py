from __future__ import annotations

from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import Message, NormalizedRole, RawEvent

from .common import dict_value, parse_timestamp, string_value, text_and_block_types


def message_from_record(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> Message | None:
    record_type = string_value(record.get("type"))
    role = normalize_claude_role(record_type)
    message = dict_value(record.get("message"))
    content = message.get("content") if message else record.get("content")
    if content is None:
        return None

    raw_text, content_block_types = text_and_block_types(
        content,
        text_block_types={"text"},
    )
    text = (
        raw_text
        if role
        in {
            NormalizedRole.USER,
            NormalizedRole.ASSISTANT,
            NormalizedRole.SYSTEM,
        }
        else None
    )
    native_message_id = string_value(record.get("uuid")) or string_value(message.get("id"))
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=native_message_id,
        parent_message_id=string_value(record.get("parentUuid")),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=content_block_types,
        metadata={
            "claude_record_type": record_type,
            "api_message_id": string_value(message.get("id")),
            "model": string_value(message.get("model")),
            "provider": string_value(message.get("provider")),
            "stop_reason": string_value(message.get("stop_reason")),
            "stop_sequence_present": message.get("stop_sequence") is not None,
            "is_sidechain": record.get("isSidechain")
            if isinstance(record.get("isSidechain"), bool)
            else None,
            "thinking_block_count": content_block_types.count("thinking"),
        },
    )


def normalize_claude_role(record_type: str | None) -> NormalizedRole:
    if record_type == "user":
        return NormalizedRole.USER
    if record_type == "assistant":
        return NormalizedRole.ASSISTANT
    if record_type == "system":
        return NormalizedRole.SYSTEM
    return NormalizedRole.UNKNOWN


def unsupported_content_shapes(record: dict[str, Any]) -> list[tuple[int, str]]:
    message = dict_value(record.get("message"))
    content = message.get("content") if message else record.get("content")
    if isinstance(content, str) or content is None:
        return []
    if not isinstance(content, list):
        return [(-1, type(content).__name__)]

    supported = {"text", "thinking", "tool_use", "tool_result", "image", "document"}
    unsupported: list[tuple[int, str]] = []
    for block_index, block in enumerate(content):
        if not isinstance(block, dict):
            unsupported.append((block_index, type(block).__name__))
            continue
        block_type = string_value(dict_value(block).get("type"))
        if block_type not in supported:
            unsupported.append((block_index, block_type or "missing"))
    return unsupported
