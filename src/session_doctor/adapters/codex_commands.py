from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.normalization import canonical_command_identity
from session_doctor.privacy import hash_text
from session_doctor.schemas import CommandRun, RawEvent

from .common import int_value, string_value

EXECUTION_NAMES = {"exec", "exec_command"}
EXITED_ENVELOPE = re.compile(
    r"\AChunk ID: [^\r\n]+\r?\n"
    r"Wall time: [^\r\n]+ seconds\r?\n"
    r"Process exited with code (-?\d+)\r?\n?"
)
RUNNING_ENVELOPE = re.compile(
    r"\AChunk ID: [^\r\n]+\r?\n"
    r"Wall time: [^\r\n]+ seconds\r?\n"
    r"Process running with session ID \d+\r?\n?"
)


@dataclass(frozen=True)
class ResponseCommandSpec:
    kind: str
    command: str | None
    cwd: str | None
    invalid_reason: str | None = None


@dataclass(frozen=True)
class ResponseCommandOutput:
    exit_code: int | None
    output: str
    outcome: str


def command_run_from_event_msg(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> CommandRun:
    stdout, stderr, output_source = command_output_parts(payload)
    call_id = string_value(payload.get("call_id"))
    command = command_text(payload.get("command"))
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


def response_command_spec(payload: dict[str, Any]) -> ResponseCommandSpec | None:
    name = string_value(payload.get("name"))
    if name not in EXECUTION_NAMES:
        return None
    if name == "exec":
        command = string_value(payload.get("input"))
        return ResponseCommandSpec(
            kind=name,
            command=command if command else None,
            cwd=None,
            invalid_reason=None if command else "missing_nonempty_input",
        )

    arguments = string_value(payload.get("arguments"))
    if arguments is None:
        return ResponseCommandSpec(name, None, None, "missing_arguments")
    try:
        decoded = json.loads(arguments)
    except json.JSONDecodeError:
        return ResponseCommandSpec(name, None, None, "arguments_not_json")
    if not isinstance(decoded, dict):
        return ResponseCommandSpec(name, None, None, "arguments_not_object")
    command = string_value(decoded.get("cmd"))
    if not command:
        return ResponseCommandSpec(name, None, None, "missing_nonempty_cmd")
    return ResponseCommandSpec(name, command, string_value(decoded.get("workdir")))


def response_command_output(kind: str, payload: dict[str, Any]) -> ResponseCommandOutput:
    output = string_value(payload.get("output")) or ""
    if kind == "exec":
        return ResponseCommandOutput(exit_code=None, output=output, outcome="opaque")

    exited = EXITED_ENVELOPE.match(output)
    if exited is not None:
        return ResponseCommandOutput(
            exit_code=int(exited.group(1)),
            output=output[exited.end() :],
            outcome="exited",
        )
    running = RUNNING_ENVELOPE.match(output)
    if running is not None:
        return ResponseCommandOutput(
            exit_code=None,
            output=output[running.end() :],
            outcome="running",
        )
    return ResponseCommandOutput(exit_code=None, output=output, outcome="opaque")


def command_run_from_response_item(
    session_id: str,
    event: RawEvent,
    native_call_id: str,
    spec: ResponseCommandSpec,
    output_record: tuple[RawEvent, dict[str, Any]] | None,
    *,
    unavailable_outcome: str = "missing",
) -> CommandRun:
    if spec.command is None:
        raise ValueError("response command specification must contain a command")
    output_event = output_record[0] if output_record is not None else None
    output = (
        response_command_output(spec.kind, output_record[1])
        if output_record is not None
        else ResponseCommandOutput(exit_code=None, output="", outcome=unavailable_outcome)
    )
    identity = canonical_command_identity(spec.command)
    ended_at = (
        output_event.timestamp
        if output_event is not None and output.outcome in {"exited", "opaque"}
        else None
    )
    return CommandRun(
        command_run_id=stable_id("command_run", session_id, native_call_id),
        session_id=session_id,
        source_event_id=event.event_id,
        tool_call_id=stable_id("tool_call", session_id, native_call_id),
        command=spec.command,
        command_identity_hash=identity.identity_hash,
        command_display=identity.display,
        command_normalization=identity.normalization,
        cwd=spec.cwd,
        started_at=event.timestamp,
        ended_at=ended_at,
        exit_code=output.exit_code,
        stdout_hash=hash_text(output.output) if output.output else None,
        output_length=len(output.output),
        metadata={
            "native_format": "response_item",
            "execution_kind": spec.kind,
            "outcome": output.outcome,
            "output_source": "response_item_output",
        },
    )
