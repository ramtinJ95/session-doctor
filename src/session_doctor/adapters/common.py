from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text
from session_doctor.schemas import ParseWarning, SessionSource

from .errors import SourceFormatError, SourceReadError

JsonRecord = tuple[int, dict[str, Any]]


def read_jsonl_records(
    source: SessionSource,
    source_path: Path,
    *,
    agent_display_name: str,
) -> tuple[list[JsonRecord], list[ParseWarning]]:
    records: list[JsonRecord] = []
    warnings: list[ParseWarning] = []
    try:
        with source_path.open(encoding="utf-8") as file:
            for record_index, line in enumerate(file):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "malformed_json",
                            f"Malformed JSONL record: {exc.msg}",
                            {"line": exc.lineno, "column": exc.colno},
                        )
                    )
                    continue
                if not isinstance(parsed, dict):
                    warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "non_object_record",
                            f"{agent_display_name} record is not a JSON object",
                            {"json_type": type(parsed).__name__},
                        )
                    )
                    continue
                records.append((record_index, parsed))
    except UnicodeDecodeError as exc:
        raise SourceFormatError(
            source_path,
            f"Unable to decode {agent_display_name} source as UTF-8 at byte {exc.start}",
        ) from exc
    except OSError as exc:
        raise SourceReadError(
            source_path, f"Unable to read {agent_display_name} source: {exc}"
        ) from exc
    return records, warnings


def warning_for_record(
    source: SessionSource,
    record_index: int,
    code: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ParseWarning:
    return ParseWarning(
        warning_id=stable_id("warning", source.source_id, record_index, code),
        source_id=source.source_id,
        record_index=record_index,
        message=message,
        metadata={"code": code, **(metadata or {})},
    )


def warning_for_source(
    source: SessionSource,
    code: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ParseWarning:
    return ParseWarning(
        warning_id=stable_id("warning", source.source_id, code),
        source_id=source.source_id,
        message=message,
        metadata={"code": code, **(metadata or {})},
    )


def increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def hash_json(value: object) -> str:
    return hash_text(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def int_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def bool_value(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def dict_value(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def text_and_block_types(
    content: object,
    *,
    text_block_types: set[str] | None = None,
) -> tuple[str | None, list[str]]:
    if isinstance(content, str):
        return content, ["text"]
    if not isinstance(content, list):
        return None, []

    texts: list[str] = []
    block_types: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        typed_block = dict_value(block)
        block_type = string_value(typed_block.get("type"))
        if block_type:
            block_types.append(block_type)
        if text_block_types is not None and block_type not in text_block_types:
            continue
        block_text = string_value(typed_block.get("text"))
        if block_text is not None:
            texts.append(block_text)

    return "\n".join(texts) if texts else None, block_types


def text_from_content(
    content: object,
    *,
    text_block_types: set[str] | None = None,
) -> str | None:
    text, _ = text_and_block_types(content, text_block_types=text_block_types)
    return text


def content_blocks(content: object) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [dict_value(block) for block in content if isinstance(block, dict)]
