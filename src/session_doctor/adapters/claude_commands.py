from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.normalization import canonical_command_identity
from session_doctor.privacy import hash_text
from session_doctor.schemas import CommandRun, RawEvent

from .claude_tools import first_int, first_string, serialized_output, tool_call_id
from .common import bool_value, dict_value, string_value


@dataclass(frozen=True)
class ClaudeToolUse:
    event: RawEvent
    block: dict[str, Any]
    block_index: int
    cwd: str | None


@dataclass(frozen=True)
class ClaudeToolResult:
    event: RawEvent
    record: dict[str, Any]
    block: dict[str, Any]


def command_runs_from_tools(
    session_id: str,
    tool_uses: list[ClaudeToolUse],
    tool_results: list[ClaudeToolResult],
    *,
    session_cwd: str | None,
) -> list[CommandRun]:
    results_by_call_id = {
        call_id: result
        for result in tool_results
        if (call_id := string_value(result.block.get("tool_use_id"))) is not None
    }
    command_runs: list[CommandRun] = []
    for tool_use in tool_uses:
        if (string_value(tool_use.block.get("name")) or "").lower() != "bash":
            continue
        arguments = dict_value(tool_use.block.get("input"))
        command = string_value(arguments.get("command"))
        if command is None:
            continue
        native_tool_call_id = string_value(tool_use.block.get("id"))
        result = results_by_call_id.get(native_tool_call_id or "")
        command_runs.append(
            command_run_from_tool_use(
                session_id,
                tool_use,
                result,
                command,
                arguments,
                session_cwd=session_cwd,
            )
        )
    return command_runs


def command_run_from_tool_use(
    session_id: str,
    tool_use: ClaudeToolUse,
    result: ClaudeToolResult | None,
    command: str,
    arguments: dict[str, Any],
    *,
    session_cwd: str | None,
) -> CommandRun:
    native_tool_call_id = string_value(tool_use.block.get("id"))
    identity = canonical_command_identity(command)
    top_level_result = dict_value(result.record.get("toolUseResult")) if result else {}
    stdout = first_string(top_level_result, "stdout", "output")
    stderr = first_string(top_level_result, "stderr")
    stdout = None if stdout == "" else stdout
    stderr = None if stderr == "" else stderr
    if stdout is None and result is not None and "content" in result.block:
        stdout = serialized_output(result.block.get("content"))
        stdout = None if stdout == "" else stdout
    output_length = sum(len(value) for value in (stdout, stderr) if value is not None)
    is_error = None
    if result is not None:
        is_error = bool_value(result.block.get("is_error"))
        if is_error is None:
            is_error = bool_value(result.block.get("isError"))
    native_exit_code = first_int(top_level_result, "exitCode", "exit_code", "code")
    exit_code = 1 if native_exit_code is None and is_error is True else native_exit_code
    source_event = result.event if result is not None else tool_use.event
    return CommandRun(
        command_run_id=stable_id(
            "command_run",
            session_id,
            native_tool_call_id or tool_use.event.event_id,
            tool_use.block_index if native_tool_call_id is None else None,
        ),
        session_id=session_id,
        source_event_id=source_event.event_id,
        tool_call_id=tool_call_id(
            session_id,
            native_tool_call_id,
            tool_use.event,
            tool_use.block_index,
        ),
        command=command,
        command_identity_hash=identity.identity_hash,
        command_display=identity.display,
        command_normalization=identity.normalization,
        cwd=(
            string_value(arguments.get("cwd"))
            or string_value(arguments.get("workdir"))
            or tool_use.cwd
            or session_cwd
        ),
        started_at=tool_use.event.timestamp,
        ended_at=result.event.timestamp if result is not None else None,
        exit_code=exit_code,
        stdout_hash=hash_text(stdout) if stdout is not None else None,
        stderr_hash=hash_text(stderr) if stderr is not None else None,
        output_length=output_length if stdout is not None or stderr is not None else None,
        metadata={
            "source": "claude_tool_use",
            "native_tool_call_id": native_tool_call_id,
            "is_error": is_error,
            "exit_code_inferred_from_is_error": native_exit_code is None and is_error is True,
            "interrupted": bool_value(top_level_result.get("interrupted")),
            "duration_ms": first_int(top_level_result, "durationMs", "duration_ms"),
            "result_observed": result is not None,
        },
    )
