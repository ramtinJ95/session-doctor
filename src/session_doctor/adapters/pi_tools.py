from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import (
    CommandRun,
    FileActivity,
    ModelUsage,
    RawEvent,
    ToolCall,
    ToolResult,
)

from .common import (
    bool_value,
    dict_value,
    int_value,
    string_value,
    text_from_content,
)
from .patches import apply_patch_file_changes


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


def command_run_from_tool_result(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
    tool_call_arguments_by_id: dict[str, dict[str, Any]],
) -> CommandRun | None:
    message_payload = dict_value(record.get("message"))
    tool_name = string_value(message_payload.get("toolName"))
    if tool_name not in {"bash", "exec_command"}:
        return None
    call_id = string_value(message_payload.get("toolCallId"))
    arguments = tool_call_arguments_by_id.get(call_id or "", {})
    command = command_from_tool_arguments(tool_name, arguments)
    if command is None:
        return None
    cwd = string_value(arguments.get("workdir")) or string_value(arguments.get("cwd"))
    output = tool_result_output(message_payload) or ""
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=command,
        cwd=cwd,
        ended_at=event.timestamp,
        exit_code=exit_code_from_tool_result(message_payload),
        stdout_hash=hash_text(output) if output else None,
        output_length=len(output),
        metadata={
            "source": "toolResult",
            "tool_name": tool_name,
            "is_error": bool_value(message_payload.get("isError")),
        },
    )


def command_from_tool_arguments(tool_name: str | None, arguments: dict[str, Any]) -> str | None:
    if tool_name == "exec_command":
        return string_value(arguments.get("cmd")) or string_value(arguments.get("command"))
    return string_value(arguments.get("command"))


def command_run_from_bash_execution(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
    tool_call_id_by_tool_result_id: dict[str, str],
) -> CommandRun:
    message_payload = dict_value(record.get("message"))
    output = string_value(message_payload.get("output")) or ""
    parent_id = string_value(record.get("parentId"))
    call_id = tool_call_id_by_tool_result_id.get(parent_id or "")
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=string_value(message_payload.get("command")) or "",
        ended_at=event.timestamp,
        exit_code=int_value(message_payload.get("exitCode")),
        stdout_hash=hash_text(output) if output else None,
        output_length=len(output),
        metadata={
            "source": "bashExecution",
            "cancelled": bool_value(message_payload.get("cancelled")),
            "truncated": bool_value(message_payload.get("truncated")),
            "exclude_from_context": bool_value(message_payload.get("excludeFromContext")),
        },
    )


def file_activities_from_tool_call(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
) -> list[FileActivity]:
    tool_name = string_value(block.get("name"))
    if tool_name not in {"apply_patch", "edit", "read", "write"}:
        return []
    arguments = arguments_from_tool_call_block(block)
    if tool_name == "apply_patch":
        return file_activities_from_apply_patch(session_id, event, block, block_index, arguments)
    path = string_value(arguments.get("path"))
    if path is None:
        return []
    operation = file_activity_operation(tool_name)
    content_payload = file_content_payload(tool_name, arguments)
    return [
        FileActivity(
            file_activity_id=stable_id(
                "file_activity",
                session_id,
                event.event_id,
                string_value(block.get("id")) or block_index,
                tool_name,
                path,
            ),
            session_id=session_id,
            source_event_id=event.event_id,
            path=path,
            operation=operation,
            timestamp=event.timestamp,
            content_hash=hash_text(content_payload) if content_payload else None,
            metadata={
                "tool_call_id": string_value(block.get("id")),
                "argument_keys": sorted(arguments.keys()),
                "content_length": text_length(content_payload),
            },
        )
    ]


def file_activity_operation(tool_name: str) -> str:
    if tool_name == "edit":
        return "update"
    return tool_name


def file_activities_from_apply_patch(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
    arguments: dict[str, Any],
) -> list[FileActivity]:
    patch_text = string_value(arguments.get("input")) or string_value(arguments.get("patch"))
    if patch_text is None:
        return []
    activities: list[FileActivity] = []
    for change_index, change in enumerate(apply_patch_file_changes(patch_text)):
        content_payload = json.dumps(
            {
                "added_lines": change.added_lines,
                "operation": change.operation,
                "removed_lines": change.removed_lines,
            },
            sort_keys=True,
        )
        activities.append(
            FileActivity(
                file_activity_id=stable_id(
                    "file_activity",
                    session_id,
                    event.event_id,
                    string_value(block.get("id")) or block_index,
                    "apply_patch",
                    change.path,
                    change_index,
                ),
                session_id=session_id,
                source_event_id=event.event_id,
                path=change.path,
                operation=change.operation,
                timestamp=event.timestamp,
                content_hash=hash_text(content_payload),
                metadata={
                    "tool_call_id": string_value(block.get("id")),
                    "argument_keys": sorted(arguments.keys()),
                    "content_length": text_length(content_payload),
                    "patch_added_lines": change.added_lines,
                    "patch_removed_lines": change.removed_lines,
                },
            )
        )
    return activities


def model_usage_from_message(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> ModelUsage | None:
    message_payload = dict_value(record.get("message"))
    usage = dict_value(message_payload.get("usage"))
    if not usage:
        return None
    cost = cost_from_usage(usage)
    return ModelUsage(
        model_usage_id=stable_id("model_usage", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        timestamp=event.timestamp,
        provider=string_value(message_payload.get("provider")),
        model=string_value(message_payload.get("model")),
        input_tokens=int_value(usage.get("input")),
        output_tokens=int_value(usage.get("output")),
        cache_read_tokens=int_value(usage.get("cacheRead")),
        cache_write_tokens=int_value(usage.get("cacheWrite")),
        total_tokens=int_value(usage.get("totalTokens")),
        cost=cost,
        metadata={
            "cost": usage.get("cost"),
            "stop_reason": string_value(message_payload.get("stopReason")),
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


def tool_result_output(message_payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    content_text = text_from_content(message_payload.get("content"), text_block_types={"text"})
    if content_text:
        parts.append(content_text)
    details_text = text_from_details(dict_value(message_payload.get("details")))
    if details_text:
        parts.append(details_text)
    return "\n".join(parts) if parts else None


DETAIL_TEXT_KEYS = {
    "aggregated_output",
    "content",
    "diff",
    "error",
    "errorMessage",
    "formatted_output",
    "message",
    "output",
    "patch",
    "result",
    "stderr",
    "stdout",
    "text",
}


def text_from_details(value: object) -> str | None:
    texts: list[str] = []
    collect_detail_text(value, texts, depth=0)
    return "\n".join(texts) if texts else None


def collect_detail_text(value: object, texts: list[str], *, depth: int) -> None:
    if depth > 2:
        return
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key in DETAIL_TEXT_KEYS:
                nested_text = string_value(nested_value)
                if nested_text:
                    texts.append(nested_text)
            if isinstance(nested_value, dict | list):
                collect_detail_text(nested_value, texts, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict | list):
                collect_detail_text(item, texts, depth=depth + 1)


def tool_result_is_error(message_payload: dict[str, Any]) -> bool | None:
    message_is_error = bool_value(message_payload.get("isError"))
    if message_is_error is True:
        return True
    if details_have_failure_signal(dict_value(message_payload.get("details"))):
        return True
    return message_is_error


DETAIL_FAILURE_STATUS_VALUES = {
    "cancelled",
    "canceled",
    "error",
    "errored",
    "failed",
    "failure",
    "timed_out",
    "timeout",
}

DETAIL_FAILURE_BOOL_KEYS = {
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
    "isError",
    "is_error",
    "timedOut",
    "timed_out",
}

DETAIL_FAILURE_TEXT_KEYS = {
    "error",
    "errorCode",
    "error_code",
    "errorMessage",
    "error_message",
}

DETAIL_STATUS_KEYS = {"outcome", "state", "status"}
DETAIL_SUCCESS_BOOL_KEYS = {"ok", "success"}


def details_have_failure_signal(value: object, *, depth: int = 0) -> bool:
    if depth > 2 or not isinstance(value, dict):
        return False
    payload = dict_value(value)
    for key, nested_value in payload.items():
        if key in ("exitCode", "exit_code"):
            exit_code = int_value(nested_value)
            if exit_code is not None and exit_code != 0:
                return True
        if key in DETAIL_FAILURE_BOOL_KEYS and bool_value(nested_value) is True:
            return True
        if key in DETAIL_SUCCESS_BOOL_KEYS and bool_value(nested_value) is False:
            return True
        if key in DETAIL_FAILURE_TEXT_KEYS and string_value(nested_value):
            return True
        if key in DETAIL_STATUS_KEYS:
            status = string_value(nested_value)
            if status and status.lower().replace("-", "_") in DETAIL_FAILURE_STATUS_VALUES:
                return True
        if isinstance(nested_value, dict) and details_have_failure_signal(
            nested_value,
            depth=depth + 1,
        ):
            return True
        if isinstance(nested_value, list):
            for item in nested_value:
                if isinstance(item, dict) and details_have_failure_signal(
                    item,
                    depth=depth + 1,
                ):
                    return True
    return False


def exit_code_from_tool_result(message_payload: dict[str, Any]) -> int | None:
    details = dict_value(message_payload.get("details"))
    exit_code = exit_code_from_details(details)
    if exit_code is not None:
        return exit_code
    is_error = bool_value(message_payload.get("isError"))
    if is_error is True:
        return 1
    if is_error is False:
        return 0
    return None


def exit_code_from_details(value: object, *, depth: int = 0) -> int | None:
    if depth > 2:
        return None
    if not isinstance(value, dict):
        return None
    payload = dict_value(value)
    for key in ("exitCode", "exit_code"):
        exit_code = int_value(payload.get(key))
        if exit_code is not None:
            return exit_code
    for nested_value in payload.values():
        if isinstance(nested_value, dict):
            exit_code = exit_code_from_details(nested_value, depth=depth + 1)
            if exit_code is not None:
                return exit_code
    return None


def bash_execution_parent_record_ids(records: list[tuple[int, dict[str, Any]]]) -> set[str]:
    parent_ids: set[str] = set()
    for _, record in records:
        message_payload = dict_value(record.get("message"))
        if string_value(message_payload.get("role")) != "bashExecution":
            continue
        parent_id = string_value(record.get("parentId"))
        if parent_id is not None:
            parent_ids.add(parent_id)
    return parent_ids


def cost_from_usage(usage: dict[str, Any]) -> Decimal | None:
    cost = usage.get("cost")
    if isinstance(cost, dict):
        cost = cost.get("total")
    return decimal_value(cost)


def decimal_value(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def file_content_payload(tool_name: str | None, arguments: dict[str, Any]) -> str | None:
    if tool_name == "write":
        return string_value(arguments.get("content"))
    if tool_name != "edit":
        return None
    top_level_old_text = string_value(arguments.get("oldText"))
    top_level_new_text = string_value(arguments.get("newText"))
    if top_level_old_text is not None or top_level_new_text is not None:
        return json.dumps(
            [
                {
                    "old_length": text_length(top_level_old_text),
                    "new_length": text_length(top_level_new_text),
                }
            ],
            sort_keys=True,
        )
    edits = arguments.get("edits")
    if not isinstance(edits, list):
        return None
    safe_edits = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        edit_payload = dict_value(edit)
        safe_edits.append(
            {
                "old_length": text_length(string_value(edit_payload.get("old_string"))),
                "new_length": text_length(string_value(edit_payload.get("new_string"))),
            }
        )
    return json.dumps(safe_edits, sort_keys=True) if safe_edits else None
