from __future__ import annotations

from typing import Any

from session_doctor.ids import stable_id
from session_doctor.normalization import canonical_file_identity
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import FileActivity, RawEvent

from .common import bool_value, dict_value, string_value


def file_activities_from_patch_event(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
    *,
    cwd: str | None,
    project_path: str | None,
) -> list[FileActivity]:
    changes = dict_value(payload.get("changes"))
    activities: list[FileActivity] = []
    for path, change_payload in changes.items():
        change = dict_value(change_payload)
        diff = string_value(change.get("unified_diff"))
        identity = canonical_file_identity(path, cwd=cwd, project_path=project_path)
        activities.append(
            FileActivity(
                file_activity_id=stable_id("file_activity", session_id, event.event_id, path),
                session_id=session_id,
                source_event_id=event.event_id,
                path=path,
                normalized_path=identity.normalized_path,
                canonical_path=identity.canonical_path,
                project_relative_path=identity.project_relative_path,
                path_resolution=identity.resolution,
                operation=string_value(change.get("type")) or "patch",
                timestamp=event.timestamp,
                content_hash=hash_text(diff) if diff is not None else None,
                metadata={
                    "success": bool_value(payload.get("success")),
                    "status": string_value(payload.get("status")),
                    "diff_length": text_length(diff),
                    "move_path": string_value(change.get("move_path")),
                },
            )
        )
    if not activities:
        identity = canonical_file_identity("unknown", cwd=cwd, project_path=project_path)
        activities.append(
            FileActivity(
                file_activity_id=stable_id("file_activity", session_id, event.event_id, "unknown"),
                session_id=session_id,
                source_event_id=event.event_id,
                path="unknown",
                normalized_path=identity.normalized_path,
                canonical_path=identity.canonical_path,
                project_relative_path=identity.project_relative_path,
                path_resolution=identity.resolution,
                operation="patch",
                timestamp=event.timestamp,
                metadata={
                    "success": bool_value(payload.get("success")),
                    "status": string_value(payload.get("status")),
                },
            )
        )
    return activities
