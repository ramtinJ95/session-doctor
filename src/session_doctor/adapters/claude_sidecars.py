from __future__ import annotations

import codecs
import hashlib
from pathlib import Path
from typing import Any

from session_doctor.schemas import SessionSource, SourceKind, ToolResult

from .base import ParsedSessionBundle
from .claude_topology import resolve_tool_result_path
from .common import (
    dict_value,
    int_value,
    string_value,
    warning_for_record,
    warning_for_source,
)


def add_topology_warnings(
    bundle: ParsedSessionBundle,
    source: SessionSource,
) -> None:
    if source.source_kind is SourceKind.SUBSESSION:
        parent_status = string_value(source.metadata.get("claude_parent_link_status"))
        if parent_status != "linked":
            bundle.parse_warnings.append(
                warning_for_source(
                    source,
                    f"subagent_parent_{parent_status or 'missing'}",
                    "Claude Code subagent parent could not be linked deterministically",
                    {
                        "parent_link_status": parent_status,
                        "candidate_count": int_value(
                            source.metadata.get("claude_parent_candidate_count")
                        ),
                    },
                )
            )

        sidecar = dict_value(source.metadata.get("claude_subagent_metadata"))
        metadata_status = string_value(sidecar.get("status"))
        if metadata_status in {"missing", "malformed"}:
            bundle.parse_warnings.append(
                warning_for_source(
                    source,
                    f"subagent_metadata_{metadata_status}",
                    f"Claude Code subagent metadata is {metadata_status}",
                )
            )
        if string_value(sidecar.get("identity_status")) == "mismatched":
            bundle.parse_warnings.append(
                warning_for_source(
                    source,
                    "subagent_metadata_mismatched",
                    "Claude Code subagent metadata identity does not match its transcript",
                )
            )

    for metadata_key, code, message in (
        (
            "claude_orphan_subagent_metadata_count",
            "orphan_subagent_metadata",
            "Claude Code metadata sidecars have no matching subagent transcript",
        ),
        (
            "claude_orphan_tool_result_count",
            "orphan_tool_result_sidecar",
            "Claude Code tool-result sidecars have no explicit transcript reference",
        ),
        (
            "claude_malformed_orphan_metadata_count",
            "malformed_subagent_metadata",
            "Claude Code orphan subagent metadata could not be parsed",
        ),
    ):
        count = int_value(source.metadata.get(metadata_key)) or 0
        if count:
            bundle.parse_warnings.append(
                warning_for_source(
                    source,
                    code,
                    message,
                    {"count": count},
                )
            )


def enrich_tool_result_from_sidecar(
    bundle: ParsedSessionBundle,
    source: SessionSource,
    record_index: int,
    record: dict[str, Any],
    tool_result: ToolResult,
) -> ToolResult:
    native_result = dict_value(record.get("toolUseResult"))
    raw_path = string_value(native_result.get("persistedOutputPath"))
    if raw_path is None:
        return tool_result

    sidecar_path = resolve_tool_result_path(Path(source.source_path), raw_path)
    if sidecar_path is None:
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "unsafe_tool_result_sidecar_path",
                "Claude Code tool-result sidecar path is outside the session "
                "tool-results directory",
            )
        )
        return tool_result
    if not sidecar_path.is_file():
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "missing_tool_result_sidecar",
                "Claude Code transcript references a missing tool-result sidecar",
            )
        )
        return tool_result

    try:
        sidecar_hash, sidecar_byte_length, sidecar_character_length = hash_sidecar(sidecar_path)
    except OSError:
        bundle.parse_warnings.append(
            warning_for_record(
                source,
                record_index,
                "unreadable_tool_result_sidecar",
                "Claude Code tool-result sidecar could not be read",
            )
        )
        return tool_result

    declared_length = int_value(native_result.get("persistedOutputSize"))
    metadata = {
        **tool_result.metadata,
        "sidecar_correlated": True,
        "sidecar_hash": sidecar_hash,
        "sidecar_byte_length": sidecar_byte_length,
        "sidecar_character_length": sidecar_character_length,
        "sidecar_declared_length": declared_length,
        "inline_output_truncated": (
            tool_result.output_length is not None
            and sidecar_character_length is not None
            and tool_result.output_length < sidecar_character_length
        ),
    }
    use_sidecar_output = tool_result.output_length in {None, 0} and sidecar_byte_length > 0
    return tool_result.model_copy(
        update={
            "output_hash": sidecar_hash if use_sidecar_output else tool_result.output_hash,
            "output_length": (
                sidecar_character_length if use_sidecar_output else tool_result.output_length
            ),
            "metadata": metadata,
        }
    )


def hash_sidecar(path: Path) -> tuple[str, int, int | None]:
    digest = hashlib.sha256()
    byte_length = 0
    character_length: int | None = 0
    decoder = codecs.getincrementaldecoder("utf-8")()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            byte_length += len(chunk)
            if character_length is not None:
                try:
                    character_length += len(decoder.decode(chunk))
                except UnicodeDecodeError:
                    character_length = None
    if character_length is not None:
        try:
            character_length += len(decoder.decode(b"", final=True))
        except UnicodeDecodeError:
            character_length = None
    return digest.hexdigest(), byte_length, character_length


__all__ = ["add_topology_warnings", "enrich_tool_result_from_sidecar", "hash_sidecar"]
