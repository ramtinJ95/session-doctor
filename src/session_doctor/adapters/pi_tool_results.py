from __future__ import annotations

from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import RawEvent, ToolResult

from .common import dict_value, string_value
from .pi_result_heuristics import tool_result_is_error, tool_result_output


def tool_result_from_message(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> ToolResult:
    message_payload = dict_value(record.get("message"))
    call_id = string_value(message_payload.get("toolCallId"))
    output = tool_result_output(message_payload)
    details = dict_value(message_payload.get("details"))
    return ToolResult(
        tool_result_id=stable_id("tool_result", session_id, event.event_id),
        session_id=session_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        timestamp=event.timestamp,
        is_error=tool_result_is_error(message_payload),
        output_hash=hash_text(output) if output is not None else None,
        output_length=text_length(output),
        metadata={
            "tool_name": string_value(message_payload.get("toolName")),
            "details_keys": sorted(details.keys()),
            "truncation": details.get("truncation"),
        },
    )
