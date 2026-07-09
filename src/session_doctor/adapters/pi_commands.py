from __future__ import annotations

from typing import Any

from session_doctor.ids import stable_id
from session_doctor.normalization import canonical_command_identity
from session_doctor.privacy import hash_text
from session_doctor.schemas import CommandRun, RawEvent

from .common import bool_value, dict_value, int_value, string_value
from .pi_result_heuristics import exit_code_from_tool_result, tool_result_output


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
    identity = canonical_command_identity(command)
    cwd = string_value(arguments.get("workdir")) or string_value(arguments.get("cwd"))
    output = tool_result_output(message_payload) or ""
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=command,
        command_identity_hash=identity.identity_hash,
        command_display=identity.display,
        command_normalization=identity.normalization,
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
    command = string_value(message_payload.get("command")) or ""
    identity = canonical_command_identity(command)
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=command,
        command_identity_hash=identity.identity_hash,
        command_display=identity.display,
        command_normalization=identity.normalization,
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
