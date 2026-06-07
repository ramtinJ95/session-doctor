from __future__ import annotations

from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.schemas import AgentName, ParseWarning, RawEvent, SessionSource

from .common import dict_value, hash_json, parse_timestamp, read_jsonl_records, string_value


def read_codex_jsonl(
    source: SessionSource,
    source_path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], list[ParseWarning]]:
    return read_jsonl_records(source, source_path, agent_display_name="Codex")


def raw_event_for_record(
    source: SessionSource,
    session_id: str,
    record_index: int,
    record: dict[str, Any],
) -> RawEvent:
    payload = dict_value(record.get("payload"))
    payload_type = string_value(payload.get("type"))
    return RawEvent(
        event_id=stable_id("event", session_id, source.source_path, record_index),
        source_id=source.source_id,
        agent_name=AgentName.CODEX,
        record_index=record_index,
        native_event_type=string_value(record.get("type")),
        native_event_id=string_value(payload.get("id")) or string_value(payload.get("call_id")),
        native_parent_id=(
            string_value(payload.get("parent_id")) or string_value(payload.get("turn_id"))
        ),
        timestamp=parse_timestamp(string_value(record.get("timestamp"))),
        payload_hash=hash_json(record),
        metadata={
            "payload_type": payload_type,
            "payload_keys": sorted(payload.keys()),
        },
    )
