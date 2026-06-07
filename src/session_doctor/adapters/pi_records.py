from __future__ import annotations

from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.schemas import AgentName, ParseWarning, RawEvent, SessionSource

from .common import (
    JsonRecord,
    dict_value,
    hash_json,
    parse_timestamp,
    read_jsonl_records,
    string_value,
)


def read_pi_jsonl(
    source: SessionSource,
    source_path: Path,
) -> tuple[list[JsonRecord], list[ParseWarning]]:
    return read_jsonl_records(
        source,
        source_path,
        agent_display_name="Pi",
        open_error="raise",
    )


def raw_event_for_record(
    source: SessionSource,
    session_id: str,
    record_index: int,
    record: dict[str, Any],
) -> RawEvent:
    message_payload = dict_value(record.get("message"))
    return RawEvent(
        event_id=stable_id("event", session_id, source.source_path, record_index),
        source_id=source.source_id,
        agent_name=AgentName.PI,
        record_index=record_index,
        native_event_type=string_value(record.get("type")),
        native_event_id=string_value(record.get("id")),
        native_parent_id=string_value(record.get("parentId")),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        payload_hash=hash_json(record),
        metadata={
            "payload_keys": sorted(record.keys()),
            "message_role": string_value(message_payload.get("role")),
        },
    )
