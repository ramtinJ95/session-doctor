from __future__ import annotations

import json
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text
from session_doctor.schemas import RawEvent, ToolCall

from .common import dict_value, string_value


def tool_call_from_block(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
) -> ToolCall:
    call_id = string_value(block.get("id"))
    arguments = arguments_from_tool_call_block(block)
    partial_json = string_value(block.get("partialJson"))
    arguments_payload: object | None = arguments if arguments else partial_json
    arguments_json = (
        json.dumps(arguments_payload, sort_keys=True, default=str)
        if arguments_payload is not None
        else None
    )
    tool_call_id = (
        stable_id("tool_call", session_id, call_id)
        if call_id is not None
        else stable_id("tool_call", session_id, event.event_id, block_index)
    )
    return ToolCall(
        tool_call_id=tool_call_id,
        session_id=session_id,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        name=string_value(block.get("name")) or "unknown",
        timestamp=event.timestamp,
        arguments_hash=hash_text(arguments_json) if arguments_json else None,
        metadata={
            "partial_json": partial_json is not None,
            "partial_json_parseable": bool(partial_json and arguments),
            "argument_keys": sorted(arguments.keys()),
            "path": string_value(arguments.get("path")),
            "timeout": arguments.get("timeout"),
        },
    )


def arguments_from_tool_call_block(block: dict[str, Any]) -> dict[str, Any]:
    arguments = dict_value(block.get("arguments"))
    if arguments:
        return arguments
    partial_json = string_value(block.get("partialJson"))
    if partial_json is None:
        return arguments
    try:
        parsed = json.loads(partial_json)
    except json.JSONDecodeError:
        return arguments
    return dict_value(parsed)
