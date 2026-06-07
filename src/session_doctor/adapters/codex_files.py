from __future__ import annotations

from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import FileActivity, RawEvent

from .common import bool_value, dict_value, string_value


def file_activities_from_patch_event(
    session_id: str,
    event: RawEvent,
    payload: dict[str, Any],
) -> list[FileActivity]:
    changes = dict_value(payload.get("changes"))
    activities: list[FileActivity] = []
    for path, change_payload in changes.items():
        change = dict_value(change_payload)
        diff = string_value(change.get("unified_diff"))
        activities.append(
            FileActivity(
                file_activity_id=stable_id("file_activity", session_id, event.event_id, path),
                session_id=session_id,
                source_event_id=event.event_id,
                path=path,
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
        activities.append(
            FileActivity(
                file_activity_id=stable_id("file_activity", session_id, event.event_id, "unknown"),
                session_id=session_id,
                source_event_id=event.event_id,
                path="unknown",
                operation="patch",
                timestamp=event.timestamp,
                metadata={
                    "success": bool_value(payload.get("success")),
                    "status": string_value(payload.get("status")),
                },
            )
        )
    return activities
