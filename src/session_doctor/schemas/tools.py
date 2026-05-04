from __future__ import annotations

from pydantic import Field

from .common import Metadata, OptionalDatetime, SessionDoctorModel


class ToolCall(SessionDoctorModel):
    tool_call_id: str
    session_id: str
    source_event_id: str | None = None
    native_tool_call_id: str | None = None
    name: str
    timestamp: OptionalDatetime = None
    arguments_hash: str | None = None
    metadata: Metadata = Field(default_factory=dict)


class ToolResult(SessionDoctorModel):
    tool_result_id: str
    session_id: str
    tool_call_id: str | None = None
    source_event_id: str | None = None
    native_tool_call_id: str | None = None
    timestamp: OptionalDatetime = None
    is_error: bool | None = None
    output_hash: str | None = None
    output_length: int | None = None
    metadata: Metadata = Field(default_factory=dict)


class CommandRun(SessionDoctorModel):
    command_run_id: str
    session_id: str
    source_event_id: str | None = None
    tool_call_id: str | None = None
    command: str
    cwd: str | None = None
    started_at: OptionalDatetime = None
    ended_at: OptionalDatetime = None
    exit_code: int | None = None
    stdout_hash: str | None = None
    stderr_hash: str | None = None
    output_length: int | None = None
    metadata: Metadata = Field(default_factory=dict)
