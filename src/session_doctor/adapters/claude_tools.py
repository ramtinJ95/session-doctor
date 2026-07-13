from __future__ import annotations

import json
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text
from session_doctor.schemas import ModelUsage, RawEvent, ToolCall, ToolResult, UsageSemantics

from .common import bool_value, content_blocks, dict_value, int_value, string_value


def assistant_tool_use_blocks(record: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    message = dict_value(record.get("message"))
    return [
        (block_index, block)
        for block_index, block in enumerate(content_blocks(message.get("content")))
        if string_value(block.get("type")) == "tool_use"
    ]


def user_tool_result_blocks(record: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    message = dict_value(record.get("message"))
    return [
        (block_index, block)
        for block_index, block in enumerate(content_blocks(message.get("content")))
        if string_value(block.get("type")) == "tool_result"
    ]


def tool_call_id(
    session_id: str,
    native_tool_call_id: str | None,
    event: RawEvent,
    block_index: int | None = None,
) -> str:
    if native_tool_call_id is not None:
        return stable_id("tool_call", session_id, native_tool_call_id)
    return stable_id("tool_call", session_id, event.event_id, block_index)


def tool_call_from_block(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
) -> ToolCall:
    native_tool_call_id = string_value(block.get("id"))
    arguments = dict_value(block.get("input"))
    arguments_json = serialized_value(arguments)
    path = first_string(arguments, "file_path", "path", "notebook_path")
    timeout = arguments.get("timeout")
    return ToolCall(
        tool_call_id=tool_call_id(session_id, native_tool_call_id, event, block_index),
        session_id=session_id,
        source_event_id=event.event_id,
        native_tool_call_id=native_tool_call_id,
        name=string_value(block.get("name")) or "unknown",
        timestamp=event.timestamp,
        arguments_hash=hash_text(arguments_json),
        metadata={
            "argument_keys": sorted(arguments.keys()),
            "path": path,
            "timeout": timeout
            if isinstance(timeout, int | float) and not isinstance(timeout, bool)
            else None,
            "run_in_background": bool_value(arguments.get("run_in_background")),
            "command_length": len(string_value(arguments.get("command")) or ""),
        },
    )


def tool_result_from_block(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
    block: dict[str, Any],
    block_index: int,
) -> ToolResult:
    native_tool_call_id = string_value(block.get("tool_use_id"))
    content_present = "content" in block
    output = serialized_output(block.get("content")) if content_present else None
    top_level_result = dict_value(record.get("toolUseResult"))
    if output is None:
        output = output_from_tool_use_result(top_level_result)
    is_error = bool_value(block.get("is_error"))
    if is_error is None:
        is_error = bool_value(block.get("isError"))
    error_text = first_string(top_level_result, "error", "errorMessage")
    return ToolResult(
        tool_result_id=stable_id(
            "tool_result",
            session_id,
            event.event_id,
            native_tool_call_id,
            block_index,
        ),
        session_id=session_id,
        tool_call_id=(
            stable_id("tool_call", session_id, native_tool_call_id)
            if native_tool_call_id is not None
            else None
        ),
        source_event_id=event.event_id,
        native_tool_call_id=native_tool_call_id,
        timestamp=event.timestamp,
        is_error=is_error,
        output_hash=hash_text(output) if output is not None else None,
        output_length=len(output) if output is not None else None,
        metadata={
            "content_kind": type(block.get("content")).__name__ if content_present else None,
            "tool_use_result_keys": sorted(top_level_result.keys()),
            "interrupted": bool_value(top_level_result.get("interrupted")),
            "is_image": bool_value(top_level_result.get("isImage")),
            "no_output_expected": bool_value(top_level_result.get("noOutputExpected")),
            "duration_ms": first_int(top_level_result, "durationMs", "duration_ms"),
            "exit_code": first_int(top_level_result, "exitCode", "exit_code", "code"),
            "error_hash": hash_text(error_text) if error_text is not None else None,
            "error_length": len(error_text) if error_text is not None else None,
        },
    )


def model_usage_from_record(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> ModelUsage | None:
    message = dict_value(record.get("message"))
    usage = dict_value(message.get("usage"))
    if not usage:
        return None

    input_tokens = int_value(usage.get("input_tokens"))
    output_tokens = int_value(usage.get("output_tokens"))
    cache_read_tokens = int_value(usage.get("cache_read_input_tokens"))
    cache_write_tokens = int_value(usage.get("cache_creation_input_tokens"))
    token_values = (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
    total_tokens = (
        sum(value or 0 for value in token_values)
        if any(value is not None for value in token_values)
        else None
    )
    mapped_keys = {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
    return ModelUsage(
        model_usage_id=stable_id("model_usage", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        timestamp=event.timestamp,
        provider=string_value(message.get("provider")) or string_value(record.get("provider")),
        model=string_value(message.get("model")),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        total_tokens=total_tokens,
        aggregation_semantics=UsageSemantics.INCREMENTAL,
        metadata={
            "unmapped_usage_keys": sorted(set(usage) - mapped_keys),
            "service_tier": string_value(usage.get("service_tier")),
        },
    )


def output_from_tool_use_result(result: dict[str, Any]) -> str | None:
    output_parts = {
        key: value
        for key in ("stdout", "stderr", "output")
        if (value := string_value(result.get(key))) is not None
    }
    return serialized_value(output_parts) if output_parts else None


def serialized_output(value: object) -> str:
    if isinstance(value, str):
        return value
    return serialized_value(value)


def serialized_value(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = string_value(payload.get(key))
        if value is not None:
            return value
    return None


def first_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = int_value(payload.get(key))
        if value is not None:
            return value
    return None
