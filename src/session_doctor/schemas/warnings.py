from __future__ import annotations

from pydantic import Field

from .common import Metadata, SessionDoctorModel


class ParseWarning(SessionDoctorModel):
    warning_id: str
    source_id: str
    record_index: int | None = None
    severity: str = "warning"
    message: str
    metadata: Metadata = Field(default_factory=dict)
