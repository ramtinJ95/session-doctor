from __future__ import annotations

from pydantic import Field

from .common import Metadata, NormalizedRole, OptionalDatetime, SessionDoctorModel


class Message(SessionDoctorModel):
    message_id: str
    session_id: str
    role: NormalizedRole
    source_event_id: str | None = None
    native_message_id: str | None = None
    parent_message_id: str | None = None
    timestamp: OptionalDatetime = None
    text: str | None = None
    text_hash: str | None = None
    text_length: int = 0
    content_block_types: list[str] = Field(default_factory=list)
    metadata: Metadata = Field(default_factory=dict)
