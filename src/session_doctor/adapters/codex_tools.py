from __future__ import annotations

import json
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import ModelUsage, RawEvent, ToolCall, ToolResult

from .common import dict_value, int_value, string_value


def tool_call_from_response_item(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolCall:
    payload_type = string_value(payload.get("type"))
    call_id = string_value(payload.get("call_id"))
    arguments = string_value(payload.get("arguments")) or string_value(payload.get("input"))
    is_tool_search = payload_type == "tool_search_call"
    return ToolCall(
        tool_call_id=stable_id("tool_call", session_id, call_id or event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        name="tool_search" if is_tool_search else string_value(payload.get("name")) or "unknown",
        timestamp=event.timestamp,
        arguments_hash=hash_text(arguments) if arguments is not None else None,
        metadata={
            "payload_type": payload_type,
            "status": string_value(payload.get("status")),
            "argument_keys": argument_keys(arguments),
            **({"execution": string_value(payload.get("execution"))} if is_tool_search else {}),
        },
    )


def tool_call_from_web_search_call(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolCall:
    action = dict_value(payload.get("action"))
    action_json = json.dumps(action, sort_keys=True, default=str)
    return ToolCall(
        tool_call_id=stable_id("tool_call", session_id, "web_search", event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        name="web_search",
        timestamp=event.timestamp,
        arguments_hash=hash_text(action_json) if action else None,
        metadata={
            "payload_type": string_value(payload.get("type")),
            "status": string_value(payload.get("status")),
            "query": string_value(action.get("query")),
            "action_type": string_value(action.get("type")),
        },
    )


def tool_result_from_response_item(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
    *,
    link_tool_call: bool = True,
) -> ToolResult:
    call_id = string_value(payload.get("call_id"))
    payload_type = string_value(payload.get("type"))
    is_tool_search = payload_type == "tool_search_output"
    output = (
        json.dumps(payload.get("tools"), sort_keys=True, default=str, separators=(",", ":"))
        if is_tool_search
        else string_value(payload.get("output"))
    )
    status = string_value(payload.get("status"))
    tools = payload.get("tools")
    tool_count = len(tools) if isinstance(tools, list) else 0
    return ToolResult(
        tool_result_id=stable_id(
            "tool_result",
            session_id,
            call_id or event.event_id,
            event.event_id,
        ),
        session_id=session_id,
        tool_call_id=(
            stable_id("tool_call", session_id, call_id) if call_id and link_tool_call else None
        ),
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        timestamp=event.timestamp,
        is_error=None if status is None else status == "failed",
        output_hash=hash_text(output) if output is not None else None,
        output_length=text_length(output),
        metadata={
            "payload_type": payload_type,
            "status": status,
            **(
                {
                    "execution": string_value(payload.get("execution")),
                    "tool_count": tool_count,
                }
                if is_tool_search
                else {}
            ),
        },
    )


def tool_result_from_web_search_end(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ToolResult:
    call_id = string_value(payload.get("call_id"))
    query = string_value(payload.get("query"))
    return ToolResult(
        tool_result_id=stable_id("tool_result", session_id, call_id or event.event_id),
        session_id=session_id,
        tool_call_id=None,
        source_event_id=event.event_id,
        native_tool_call_id=call_id,
        timestamp=event.timestamp,
        is_error=False,
        output_hash=hash_text(query) if query else None,
        output_length=text_length(query),
        metadata={
            "payload_type": string_value(payload.get("type")),
            "tool_name": "web_search",
            "query": query,
            "action_type": string_value(dict_value(payload.get("action")).get("type")),
        },
    )


def model_usage_from_token_count(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> ModelUsage:
    info = dict_value(payload.get("info"))
    last_usage = dict_value(info.get("last_token_usage"))
    total_usage = dict_value(info.get("total_token_usage"))
    usage = last_usage or total_usage
    return ModelUsage(
        model_usage_id=stable_id("model_usage", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        timestamp=event.timestamp,
        input_tokens=int_value(usage.get("input_tokens")),
        output_tokens=int_value(usage.get("output_tokens")),
        cache_read_tokens=int_value(usage.get("cached_input_tokens")),
        total_tokens=int_value(usage.get("total_tokens")),
        metadata={
            "payload_type": string_value(payload.get("type")),
            "model_context_window": info.get("model_context_window"),
            "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
            "total_token_usage": total_usage,
            "rate_limits": payload.get("rate_limits"),
        },
    )


def argument_keys(arguments: str | None) -> list[str]:
    if arguments is None:
        return []
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return sorted(str(key) for key in parsed)
    return []
