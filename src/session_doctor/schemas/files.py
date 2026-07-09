from __future__ import annotations

from pydantic import Field, model_validator

from session_doctor.normalization import canonical_file_identity

from .common import Metadata, OptionalDatetime, SessionDoctorModel


class FileActivity(SessionDoctorModel):
    file_activity_id: str
    session_id: str
    source_event_id: str | None = None
    path: str
    normalized_path: str = ""
    canonical_path: str | None = None
    project_relative_path: str | None = None
    path_resolution: str = ""
    operation: str
    timestamp: OptionalDatetime = None
    content_hash: str | None = None
    metadata: Metadata = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_unresolved_identity(self) -> FileActivity:
        if not self.normalized_path and not self.path_resolution:
            identity = canonical_file_identity(self.path, cwd=None, project_path=None)
            self.normalized_path = identity.normalized_path
            self.canonical_path = identity.canonical_path
            self.project_relative_path = identity.project_relative_path
            self.path_resolution = identity.resolution
        if not self.normalized_path or not self.path_resolution:
            raise ValueError("file identity fields must be populated together")
        return self
