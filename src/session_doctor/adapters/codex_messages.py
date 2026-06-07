from __future__ import annotations

from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import Message, NormalizedRole, RawEvent

from .common import parse_timestamp, string_value, text_and_block_types

CODEX_MESSAGE_SOURCE_RESPONSE_ITEM = "response_item"
CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK = "event_msg_fallback"


def message_from_response_item(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
    timestamp: str | None,
) -> Message:
    text, content_block_types = text_and_block_types(payload.get("content"))
    role = normalize_codex_role(string_value(payload.get("role")))
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(payload.get("id")),
        timestamp=parse_timestamp(timestamp),
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=content_block_types,
        metadata={
            "phase": string_value(payload.get("phase")),
            "codex_message_source": CODEX_MESSAGE_SOURCE_RESPONSE_ITEM,
        },
    )


def message_from_event_msg_fallback(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> Message:
    payload_type = string_value(payload.get("type"))
    text = string_value(payload.get("message")) or string_value(payload.get("text"))
    role = NormalizedRole.USER if payload_type == "user_message" else NormalizedRole.ASSISTANT
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(payload.get("id")),
        timestamp=event.timestamp,
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=["event_msg_text"] if text is not None else [],
        metadata={"codex_message_source": CODEX_MESSAGE_SOURCE_EVENT_MSG_FALLBACK},
    )


def message_identity(message: Message) -> tuple[NormalizedRole, str | None]:
    return (message.role, message.text_hash)


def has_nearby_response_message(
    record_index: int,
    identity: tuple[NormalizedRole, str | None],
    response_messages: list[tuple[int, tuple[NormalizedRole, str | None]]],
) -> bool:
    return any(
        response_identity == identity and abs(response_record_index - record_index) <= 1
        for response_record_index, response_identity in response_messages
    )


def normalize_codex_role(role: str | None) -> NormalizedRole:
    if role == "user":
        return NormalizedRole.USER
    if role == "assistant":
        return NormalizedRole.ASSISTANT
    if role == "developer":
        return NormalizedRole.DEVELOPER
    return NormalizedRole.UNKNOWN
