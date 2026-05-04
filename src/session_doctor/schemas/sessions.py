from __future__ import annotations

from pydantic import Field

from .common import AgentName, Metadata, OptionalDatetime, SessionDoctorModel, SourceKind


class SessionSource(SessionDoctorModel):
    source_id: str
    agent_name: AgentName
    source_path: str
    source_kind: SourceKind = SourceKind.ROOT_SESSION
    discovered_at: OptionalDatetime = None
    native_session_id: str | None = None
    parent_source_id: str | None = None
    metadata: Metadata = Field(default_factory=dict)


class Session(SessionDoctorModel):
    session_id: str
    source_id: str
    agent_name: AgentName
    native_session_id: str | None = None
    parent_session_id: str | None = None
    started_at: OptionalDatetime = None
    ended_at: OptionalDatetime = None
    cwd: str | None = None
    project_path: str | None = None
    agent_version: str | None = None
    model_provider: str | None = None
    model: str | None = None
    is_sidechain: bool = False
    metadata: Metadata = Field(default_factory=dict)
