from __future__ import annotations

from pydantic import Field

from .common import Metadata, OptionalDatetime, SessionDoctorModel


class FileActivity(SessionDoctorModel):
    file_activity_id: str
    session_id: str
    source_event_id: str | None = None
    path: str
    operation: str
    timestamp: OptionalDatetime = None
    content_hash: str | None = None
    metadata: Metadata = Field(default_factory=dict)
