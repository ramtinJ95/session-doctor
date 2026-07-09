from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import Field

from session_doctor.ids import source_id_for_path
from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    ModelUsage,
    ParseWarning,
    RawEvent,
    Session,
    SessionDoctorModel,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.schemas.common import SourceKind


class ParsedSessionBundle(SessionDoctorModel):
    session: Session | None = None
    raw_events: list[RawEvent] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    command_runs: list[CommandRun] = Field(default_factory=list)
    file_activities: list[FileActivity] = Field(default_factory=list)
    model_usage: list[ModelUsage] = Field(default_factory=list)
    parse_warnings: list[ParseWarning] = Field(default_factory=list)


class BaseAdapter(ABC):
    name: AgentName
    display_name: str
    version = "0.1.0"
    ingestible_source_kinds = (SourceKind.ROOT_SESSION,)

    @abstractmethod
    def default_roots(self) -> tuple[Path, ...]:
        raise NotImplementedError

    @abstractmethod
    def discover(self, root: Path | None = None) -> list[SessionSource]:
        raise NotImplementedError

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
        msg = f"{self.display_name} parsing is not implemented yet."
        raise NotImplementedError(msg)

    def source_for_path(self, path: Path) -> SessionSource:
        return SessionSource(
            source_id=source_id_for_path(self.name, path),
            agent_name=self.name,
            source_path=str(path),
            source_kind=SourceKind.ROOT_SESSION,
        )

    def root_for_discovery(self, root: Path | None = None) -> Path:
        if root is not None:
            return root.expanduser()
        return self.default_roots()[0]
