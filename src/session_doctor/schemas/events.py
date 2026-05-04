from __future__ import annotations

from pydantic import Field

from .common import AgentName, Metadata, OptionalDatetime, SessionDoctorModel


class RawEvent(SessionDoctorModel):
    event_id: str
    source_id: str
    agent_name: AgentName
    record_index: int
    native_event_type: str | None = None
    native_event_id: str | None = None
    native_parent_id: str | None = None
    timestamp: OptionalDatetime = None
    payload_hash: str | None = None
    metadata: Metadata = Field(default_factory=dict)
