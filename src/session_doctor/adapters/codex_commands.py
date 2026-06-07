from __future__ import annotations

import shlex
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text
from session_doctor.schemas import CommandRun, RawEvent

from .common import int_value, string_value


def command_run_from_event_msg(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> CommandRun:
    stdout, stderr, output_source = command_output_parts(payload)
    call_id = string_value(payload.get("call_id"))
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, event.event_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, call_id) if call_id else None,
        command=command_text(payload.get("command")),
        cwd=string_value(payload.get("cwd")),
        ended_at=event.timestamp,
        exit_code=int_value(payload.get("exit_code")),
        stdout_hash=hash_text(stdout) if stdout else None,
        stderr_hash=hash_text(stderr) if stderr else None,
        output_length=len(stdout) + len(stderr),
        metadata={
            "status": string_value(payload.get("status")),
            "duration": payload.get("duration"),
            "process_id": payload.get("process_id"),
            "output_source": output_source,
        },
    )


def command_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return shlex.join(str(part) for part in value)
    return ""


def command_output_parts(payload: dict[str, Any]) -> tuple[str, str, str]:
    stdout = string_value(payload.get("stdout")) or ""
    stderr = string_value(payload.get("stderr")) or ""
    if stdout or stderr:
        return stdout, stderr, "stdout_stderr"

    aggregated_output = string_value(payload.get("aggregated_output")) or ""
    if aggregated_output:
        return aggregated_output, "", "aggregated_output"

    formatted_output = string_value(payload.get("formatted_output")) or ""
    if formatted_output:
        return formatted_output, "", "formatted_output"

    return "", "", "empty"
