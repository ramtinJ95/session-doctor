from __future__ import annotations

import json
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.normalization import canonical_file_identity
from session_doctor.privacy import hash_text
from session_doctor.schemas import FileActivity, RawEvent

from .claude_tools import first_string
from .common import bool_value, dict_value, string_value

FILE_TOOL_OPERATIONS = {
    "read": "read",
    "edit": "update",
    "multiedit": "update",
    "notebookedit": "update",
    "write": "write",
}


def file_activity_from_tool_use(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
    *,
    cwd: str | None,
    project_path: str | None,
) -> FileActivity | None:
    tool_name = string_value(block.get("name")) or ""
    operation = FILE_TOOL_OPERATIONS.get(tool_name.lower())
    if operation is None:
        return None
    arguments = dict_value(block.get("input"))
    path = first_string(arguments, "file_path", "path", "notebook_path")
    if path is None:
        return None

    identity = canonical_file_identity(path, cwd=cwd, project_path=project_path)
    content_fields = content_fields_for_tool(tool_name, arguments)
    content_payload = (
        json.dumps(content_fields, sort_keys=True, separators=(",", ":"))
        if content_fields
        else None
    )
    native_tool_call_id = string_value(block.get("id"))
    return FileActivity(
        file_activity_id=stable_id(
            "file_activity",
            session_id,
            event.event_id,
            native_tool_call_id if native_tool_call_id is not None else block_index,
            tool_name,
            path,
        ),
        session_id=session_id,
        source_event_id=event.event_id,
        path=path,
        normalized_path=identity.normalized_path,
        canonical_path=identity.canonical_path,
        project_relative_path=identity.project_relative_path,
        path_resolution=identity.resolution,
        operation=operation,
        timestamp=event.timestamp,
        content_hash=hash_text(content_payload) if content_payload is not None else None,
        metadata={
            "native_tool_name": tool_name,
            "native_tool_call_id": native_tool_call_id,
            "argument_keys": sorted(arguments.keys()),
            "content_lengths": {key: len(value) for key, value in content_fields.items()},
            "replace_all": first_bool(arguments, "replace_all", "replaceAll"),
        },
    )


def first_bool(arguments: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = bool_value(arguments.get(key))
        if value is not None:
            return value
    return None


def content_fields_for_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, str]:
    normalized_tool_name = tool_name.lower()
    if normalized_tool_name == "write":
        content = string_value(arguments.get("content"))
        return {"content": content} if content is not None else {}
    if normalized_tool_name not in {"edit", "multiedit", "notebookedit"}:
        return {}
    return {
        key: value
        for key in ("old_string", "new_string", "oldString", "newString", "new_source")
        if (value := string_value(arguments.get(key))) is not None
    }
