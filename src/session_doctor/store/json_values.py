from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, sort_keys=True, default=str)


def duckdb_value(value: object) -> object:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_metadata(payload: object) -> dict[str, Any]:
    if not isinstance(payload, str):
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_string_list(payload: object) -> list[str]:
    if not isinstance(payload, str):
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]
