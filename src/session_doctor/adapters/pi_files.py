from __future__ import annotations

import json
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.normalization import canonical_file_identity
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import FileActivity, RawEvent

from .common import dict_value, string_value
from .patches import apply_patch_file_changes
from .pi_tool_calls import arguments_from_tool_call_block


def file_activities_from_tool_call(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
    *,
    cwd: str | None,
    project_path: str | None,
) -> list[FileActivity]:
    tool_name = string_value(block.get("name"))
    if tool_name not in {"apply_patch", "edit", "read", "write"}:
        return []
    arguments = arguments_from_tool_call_block(block)
    if tool_name == "apply_patch":
        return file_activities_from_apply_patch(
            session_id,
            event,
            block,
            block_index,
            arguments,
            cwd=cwd,
            project_path=project_path,
        )
    path = string_value(arguments.get("path"))
    if path is None:
        return []
    operation = file_activity_operation(tool_name)
    content_payload = file_content_payload(tool_name, arguments)
    identity = canonical_file_identity(path, cwd=cwd, project_path=project_path)
    return [
        FileActivity(
            file_activity_id=stable_id(
                "file_activity",
                session_id,
                event.event_id,
                string_value(block.get("id")) or block_index,
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
            content_hash=hash_text(content_payload) if content_payload else None,
            metadata={
                "tool_call_id": string_value(block.get("id")),
                "argument_keys": sorted(arguments.keys()),
                "content_length": text_length(content_payload),
            },
        )
    ]


def file_activity_operation(tool_name: str) -> str:
    if tool_name == "edit":
        return "update"
    return tool_name


def file_activities_from_apply_patch(
    session_id: str,
    event: RawEvent,
    block: dict[str, Any],
    block_index: int,
    arguments: dict[str, Any],
    *,
    cwd: str | None,
    project_path: str | None,
) -> list[FileActivity]:
    patch_text = string_value(arguments.get("input")) or string_value(arguments.get("patch"))
    if patch_text is None:
        return []
    activities: list[FileActivity] = []
    for change_index, change in enumerate(apply_patch_file_changes(patch_text)):
        identity = canonical_file_identity(
            change.path,
            cwd=cwd,
            project_path=project_path,
        )
        content_payload = json.dumps(
            {
                "added_lines": change.added_lines,
                "operation": change.operation,
                "removed_lines": change.removed_lines,
            },
            sort_keys=True,
        )
        activities.append(
            FileActivity(
                file_activity_id=stable_id(
                    "file_activity",
                    session_id,
                    event.event_id,
                    string_value(block.get("id")) or block_index,
                    "apply_patch",
                    change.path,
                    change_index,
                ),
                session_id=session_id,
                source_event_id=event.event_id,
                path=change.path,
                normalized_path=identity.normalized_path,
                canonical_path=identity.canonical_path,
                project_relative_path=identity.project_relative_path,
                path_resolution=identity.resolution,
                operation=change.operation,
                timestamp=event.timestamp,
                content_hash=hash_text(content_payload),
                metadata={
                    "tool_call_id": string_value(block.get("id")),
                    "argument_keys": sorted(arguments.keys()),
                    "content_length": text_length(content_payload),
                    "patch_added_lines": change.added_lines,
                    "patch_removed_lines": change.removed_lines,
                },
            )
        )
    return activities


def file_content_payload(tool_name: str | None, arguments: dict[str, Any]) -> str | None:
    if tool_name == "write":
        return string_value(arguments.get("content"))
    if tool_name != "edit":
        return None
    top_level_old_text = string_value(arguments.get("oldText"))
    top_level_new_text = string_value(arguments.get("newText"))
    if top_level_old_text is not None or top_level_new_text is not None:
        return json.dumps(
            [
                {
                    "old_length": text_length(top_level_old_text),
                    "new_length": text_length(top_level_new_text),
                }
            ],
            sort_keys=True,
        )
    edits = arguments.get("edits")
    if not isinstance(edits, list):
        return None
    safe_edits = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        edit_payload = dict_value(edit)
        safe_edits.append(
            {
                "old_length": text_length(string_value(edit_payload.get("old_string"))),
                "new_length": text_length(string_value(edit_payload.get("new_string"))),
            }
        )
    return json.dumps(safe_edits, sort_keys=True) if safe_edits else None
