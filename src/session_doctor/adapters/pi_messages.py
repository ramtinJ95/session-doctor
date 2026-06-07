from __future__ import annotations

import json
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import Message, NormalizedRole, RawEvent

from .common import content_blocks, dict_value, parse_timestamp, string_value, text_and_block_types


def message_from_record(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> Message:
    message_payload = dict_value(record.get("message"))
    raw_text, content_block_types = text_and_block_types(
        message_payload.get("content"),
        text_block_types={"text"},
    )
    role = normalize_pi_role(string_value(message_payload.get("role")))
    text = raw_text if role in {NormalizedRole.USER, NormalizedRole.ASSISTANT} else None
    timestamp = string_value(message_payload.get("timestamp")) or string_value(
        record.get("timestamp")
    )
    metadata: dict[str, Any] = {
        "pi_message_role": string_value(message_payload.get("role")),
        "stop_reason": string_value(message_payload.get("stopReason")),
        "model": string_value(message_payload.get("model")),
        "provider": string_value(message_payload.get("provider")),
    }
    phase = phase_from_content(message_payload.get("content"))
    if phase is not None:
        metadata["phase"] = phase
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
        metadata=metadata,
    )


def phase_from_content(content: object) -> str | None:
    for block in content_blocks(content):
        if string_value(block.get("type")) != "text":
            continue
        phase = phase_from_metadata(block)
        if phase is not None:
            return phase
        signature = block.get("signature")
        if isinstance(signature, str):
            try:
                signature = json.loads(signature)
            except json.JSONDecodeError:
                continue
        phase = phase_from_metadata(signature)
        if phase is not None:
            return phase
    return None


def phase_from_metadata(value: object) -> str | None:
    payload = dict_value(value)
    phase = string_value(payload.get("phase"))
    if phase is not None:
        return phase
    metadata = dict_value(payload.get("metadata"))
    return string_value(metadata.get("phase"))


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
