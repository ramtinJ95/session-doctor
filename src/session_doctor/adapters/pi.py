from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from session_doctor.ids import source_id_for_path, stable_id
from session_doctor.privacy import hash_text, text_length
from session_doctor.schemas import (
    AgentName,
    Message,
    NormalizedRole,
    ParseWarning,
    RawEvent,
    Session,
    SessionSource,
    SourceKind,
)

from .base import BaseAdapter, ParsedSessionBundle
from .common import (
    JsonRecord,
    content_blocks,
    dict_value,
    hash_json,
    increment_count,
    parse_timestamp,
    read_jsonl_records,
    string_value,
    text_and_block_types,
    warning_for_record,
    warning_for_source,
)
from .pi_tools import (
    arguments_from_tool_call_block,
    bash_execution_parent_record_ids,
    command_run_from_bash_execution,
    command_run_from_tool_result,
    file_activities_from_tool_call,
    model_usage_from_message,
    tool_call_from_block,
    tool_result_from_message,
)

PI_METADATA_ONLY_TYPES = {
    "branch_summary",
    "compaction",
    "custom",
    "custom_message",
    "label",
    "model_change",
    "session_info",
    "thinking_level_change",
}


@dataclass
class PiCommandCorrelation:
    tool_call_arguments_by_id: dict[str, dict[str, Any]]
    tool_call_id_by_tool_result_id: dict[str, str]
    bash_execution_parent_ids: set[str]

    @classmethod
    def from_records(cls, records: list[JsonRecord]) -> PiCommandCorrelation:
        return cls(
            tool_call_arguments_by_id={},
            tool_call_id_by_tool_result_id={},
            bash_execution_parent_ids=bash_execution_parent_record_ids(records),
        )

    def remember_tool_call_arguments(
        self,
        native_tool_call_id: str | None,
        block: dict[str, Any],
    ) -> None:
        if native_tool_call_id is None:
            return
        self.tool_call_arguments_by_id[native_tool_call_id] = arguments_from_tool_call_block(block)

    def remember_tool_result_link(self, record: dict[str, Any]) -> None:
        message_payload = dict_value(record.get("message"))
        call_id = string_value(message_payload.get("toolCallId"))
        native_tool_result_id = string_value(record.get("id"))
        if call_id and native_tool_result_id:
            self.tool_call_id_by_tool_result_id[native_tool_result_id] = call_id

    def has_bash_execution_result(self, record: dict[str, Any]) -> bool:
        native_tool_result_id = string_value(record.get("id"))
        return native_tool_result_id in self.bash_execution_parent_ids


class PiAdapter(BaseAdapter):
    name = AgentName.PI
    display_name = "Pi"

    def default_roots(self) -> tuple[Path, ...]:
        return (Path.home() / ".pi" / "agent" / "sessions",)

    def discover(self, root: Path | None = None) -> list[SessionSource]:
        discovery_root = self.root_for_discovery(root)
        if not discovery_root.exists():
            return []

        return [
            SessionSource(
                source_id=source_id_for_path(self.name, path),
                agent_name=self.name,
                source_path=str(path),
                source_kind=SourceKind.ROOT_SESSION,
                metadata={"relative_path": str(path.relative_to(discovery_root))},
            )
            for path in sorted(discovery_root.rglob("*.jsonl"))
            if path.is_file()
        ]

    def parse_source(self, source: SessionSource) -> ParsedSessionBundle:
        source_path = Path(source.source_path).expanduser()
        valid_records, malformed_warnings = read_pi_jsonl(source, source_path)
        session_metadata = extract_session_metadata(source, source_path, valid_records)
        bundle = ParsedSessionBundle(
            session=session_metadata.session,
            parse_warnings=malformed_warnings,
        )
        if not has_usable_session_record(valid_records):
            bundle.parse_warnings.append(
                warning_for_source(
                    source,
                    "missing_session_record",
                    "Pi source is missing a usable session record",
                    {"source_path": str(source_path)},
                )
            )
        metadata_only_counts: dict[str, int] = {}
        command_correlation = PiCommandCorrelation.from_records(valid_records)

        for record_index, record in valid_records:
            record_type = string_value(record.get("type"))
            event = raw_event_for_record(source, session_metadata.session_id, record_index, record)
            bundle.raw_events.append(event)

            if record_type == "message":
                message_payload = dict_value(record.get("message"))
                role = normalize_pi_role(string_value(message_payload.get("role")))
                if role is NormalizedRole.UNKNOWN:
                    bundle.parse_warnings.append(
                        warning_for_record(
                            source,
                            record_index,
                            "unsupported_message_role",
                            "Unsupported Pi message role",
                            {"role": string_value(message_payload.get("role"))},
                        )
                    )
                    continue
                if role is NormalizedRole.ASSISTANT:
                    for block_index, block in enumerate(
                        content_blocks(message_payload.get("content"))
                    ):
                        block_type = string_value(block.get("type"))
                        if block_type != "toolCall":
                            continue
                        tool_call = tool_call_from_block(
                            session_metadata.session_id,
                            event,
                            block,
                            block_index,
                        )
                        bundle.tool_calls.append(tool_call)
                        command_correlation.remember_tool_call_arguments(
                            tool_call.native_tool_call_id,
                            block,
                        )
                        bundle.file_activities.extend(
                            file_activities_from_tool_call(
                                session_metadata.session_id,
                                event,
                                block,
                                block_index,
                            )
                        )
                    usage = model_usage_from_message(session_metadata.session_id, event, record)
                    if usage:
                        bundle.model_usage.append(usage)
                elif string_value(message_payload.get("role")) == "toolResult":
                    command_correlation.remember_tool_result_link(record)
                    bundle.tool_results.append(
                        tool_result_from_message(session_metadata.session_id, event, record)
                    )
                    if not command_correlation.has_bash_execution_result(record):
                        command_run = command_run_from_tool_result(
                            session_metadata.session_id,
                            event,
                            record,
                            command_correlation.tool_call_arguments_by_id,
                        )
                        if command_run:
                            bundle.command_runs.append(command_run)
                elif string_value(message_payload.get("role")) == "bashExecution":
                    bundle.command_runs.append(
                        command_run_from_bash_execution(
                            session_metadata.session_id,
                            event,
                            record,
                            command_correlation.tool_call_id_by_tool_result_id,
                        )
                    )
                bundle.messages.append(
                    message_from_record(session_metadata.session_id, event, record)
                )
            elif record_type == "session":
                continue
            elif record_type in PI_METADATA_ONLY_TYPES:
                increment_count(metadata_only_counts, record_type or "missing")
            else:
                bundle.parse_warnings.append(
                    warning_for_record(
                        source,
                        record_index,
                        "unsupported_record_type",
                        f"Unsupported Pi record type: {record_type}",
                        {"record_type": record_type},
                    )
                )

        if bundle.session:
            bundle.session.metadata["pi_metadata_only_counts"] = metadata_only_counts

        return bundle


class PiSessionMetadata:
    def __init__(self, session: Session, session_id: str) -> None:
        self.session = session
        self.session_id = session_id


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


def extract_session_metadata(
    source: SessionSource,
    source_path: Path,
    records: list[tuple[int, dict[str, Any]]],
) -> PiSessionMetadata:
    session_record: dict[str, Any] = {}
    model_changes: list[dict[str, Any]] = []
    timestamps: list[datetime] = []

    for _, record in records:
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp:
            timestamps.append(timestamp)
        record_type = string_value(record.get("type"))
        if record_type == "session":
            session_record = record
        elif record_type == "model_change":
            model_changes.append(record)

    latest_model_change = model_changes[-1] if model_changes else {}
    native_session_id = string_value(session_record.get("id")) or session_id_from_filename(
        source_path
    )
    session_id = stable_id("session", AgentName.PI.value, source.source_path, native_session_id)
    cwd = string_value(session_record.get("cwd"))
    model = string_value(latest_model_change.get("modelId"))
    source_path_project_hint = project_hint_from_source_path(source_path)

    session = Session(
        session_id=session_id,
        source_id=source.source_id,
        agent_name=AgentName.PI,
        native_session_id=native_session_id,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        cwd=cwd,
        project_path=cwd,
        agent_version=string_value(session_record.get("version")),
        model_provider=string_value(latest_model_change.get("provider")),
        model=model,
        metadata={
            "source_path": source.source_path,
            "source_path_project_hint": source_path_project_hint,
            "model_changes": [
                {
                    "provider": string_value(record.get("provider")),
                    "model": string_value(record.get("modelId")),
                    "timestamp": string_value(record.get("timestamp")),
                }
                for record in model_changes
            ],
        },
    )
    return PiSessionMetadata(session=session, session_id=session_id)


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


def message_from_record(
    session_id: str,
    event: RawEvent,
    record: dict[str, Any],
) -> Message:
    message_payload = dict_value(record.get("message"))
    raw_text, content_block_types = text_and_block_types(
        message_payload.get("content"),
        text_block_types={"text"},
    )
    role = normalize_pi_role(string_value(message_payload.get("role")))
    text = raw_text if role in {NormalizedRole.USER, NormalizedRole.ASSISTANT} else None
    timestamp = string_value(message_payload.get("timestamp")) or string_value(
        record.get("timestamp")
    )
    metadata: dict[str, Any] = {
        "pi_message_role": string_value(message_payload.get("role")),
        "stop_reason": string_value(message_payload.get("stopReason")),
        "model": string_value(message_payload.get("model")),
        "provider": string_value(message_payload.get("provider")),
    }
    phase = phase_from_content(message_payload.get("content"))
    if phase is not None:
        metadata["phase"] = phase
    return Message(
        message_id=stable_id("message", session_id, event.event_id),
        session_id=session_id,
        role=role,
        source_event_id=event.event_id,
        native_message_id=string_value(record.get("id")),
        parent_message_id=string_value(record.get("parentId")),
        timestamp=parse_timestamp(timestamp),
        text=text,
        text_hash=hash_text(text) if text is not None else None,
        text_length=text_length(text),
        content_block_types=content_block_types,
        metadata=metadata,
    )


def phase_from_content(content: object) -> str | None:
    for block in content_blocks(content):
        if string_value(block.get("type")) != "text":
            continue
        phase = phase_from_metadata(block)
        if phase is not None:
            return phase
        signature = block.get("signature")
        if isinstance(signature, str):
            try:
                signature = json.loads(signature)
            except json.JSONDecodeError:
                continue
        phase = phase_from_metadata(signature)
        if phase is not None:
            return phase
    return None


def phase_from_metadata(value: object) -> str | None:
    payload = dict_value(value)
    phase = string_value(payload.get("phase"))
    if phase is not None:
        return phase
    metadata = dict_value(payload.get("metadata"))
    return string_value(metadata.get("phase"))


def normalize_pi_role(role: str | None) -> NormalizedRole:
    if role == "user":
        return NormalizedRole.USER
    if role == "assistant":
        return NormalizedRole.ASSISTANT
    if role == "toolResult":
        return NormalizedRole.TOOL
    if role == "bashExecution":
        return NormalizedRole.TOOL
    return NormalizedRole.UNKNOWN


def has_usable_session_record(records: list[tuple[int, dict[str, Any]]]) -> bool:
    return any(
        string_value(record.get("type")) == "session" and string_value(record.get("id")) is not None
        for _, record in records
    )


def session_id_from_filename(path: Path) -> str | None:
    stem = path.stem
    if "_" not in stem:
        return stem or None
    return stem.rsplit("_", maxsplit=1)[-1]


def project_hint_from_source_path(path: Path) -> str | None:
    parent_name = path.parent.name
    if not parent_name.startswith("--") or not parent_name.endswith("--"):
        return None
    candidate = parent_name.removeprefix("--").removesuffix("--").replace("-", "/")
    return f"/{candidate.strip('/')}" if candidate else None
