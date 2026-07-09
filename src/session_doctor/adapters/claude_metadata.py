from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.schemas import AgentName, Session, SessionSource

from .common import dict_value, parse_timestamp, string_value


@dataclass(frozen=True)
class ClaudeSessionMetadata:
    session: Session
    session_id: str
    native_session_ids: tuple[str, ...]


def extract_session_metadata(
    source: SessionSource,
    source_path: Path,
    records: list[tuple[int, dict[str, Any]]],
) -> ClaudeSessionMetadata:
    native_session_ids = ordered_strings(records, "sessionId")
    observed_cwds = ordered_strings(records, "cwd")
    versions = ordered_strings(records, "version")
    git_branches = ordered_strings(records, "gitBranch")
    entrypoints = ordered_strings(records, "entrypoint")
    models = ordered_message_strings(records, "model")
    providers = ordered_message_strings(records, "provider")
    timestamps = parsed_timestamps(records)

    native_session_id = native_session_ids[0] if native_session_ids else None
    filename_identity = source_path.stem or source.source_id
    native_identity = native_session_id or source.native_session_id or filename_identity
    session_id = stable_id(
        "session",
        AgentName.CLAUDE.value,
        source.source_path,
        native_identity,
    )
    cwd = first_chronological_cwd(records)

    session = Session(
        session_id=session_id,
        source_id=source.source_id,
        agent_name=AgentName.CLAUDE,
        native_session_id=native_session_id,
        started_at=min(timestamps, key=timestamp_key) if timestamps else None,
        ended_at=max(timestamps, key=timestamp_key) if timestamps else None,
        cwd=cwd,
        project_path=cwd,
        agent_version=latest_chronological_string(records, "version"),
        model_provider=latest_chronological_message_string(records, "provider"),
        model=latest_chronological_message_string(records, "model"),
        is_sidechain=False,
        metadata={
            "source_path": source.source_path,
            "observed_cwds": list(observed_cwds),
            "cwd_change_count": transition_count(records, "cwd"),
            "claude_versions": list(versions),
            "version_change_count": transition_count(records, "version"),
            "git_branches": list(git_branches),
            "entrypoints": list(entrypoints),
            "models": list(models),
            "providers": list(providers),
        },
    )
    return ClaudeSessionMetadata(
        session=session,
        session_id=session_id,
        native_session_ids=native_session_ids,
    )


def ordered_strings(
    records: list[tuple[int, dict[str, Any]]],
    key: str,
) -> tuple[str, ...]:
    values: list[str] = []
    for _, record in records:
        value = string_value(record.get(key))
        if value is not None and value not in values:
            values.append(value)
    return tuple(values)


def ordered_message_strings(
    records: list[tuple[int, dict[str, Any]]],
    key: str,
) -> tuple[str, ...]:
    values: list[str] = []
    for _, record in records:
        message = dict_value(record.get("message"))
        value = string_value(message.get(key)) or string_value(record.get(key))
        if value is not None and value not in values:
            values.append(value)
    return tuple(values)


def first_chronological_cwd(
    records: list[tuple[int, dict[str, Any]]],
) -> str | None:
    candidates: list[tuple[tuple[float, int], str]] = []
    untimed_candidates: list[tuple[int, str]] = []
    for record_index, record in records:
        cwd = string_value(record.get("cwd"))
        if cwd is None:
            continue
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp is None:
            untimed_candidates.append((record_index, cwd))
        else:
            candidates.append(((timestamp.timestamp(), record_index), cwd))
    if candidates:
        return min(candidates, key=lambda candidate: candidate[0])[1]
    if untimed_candidates:
        return min(untimed_candidates, key=lambda candidate: candidate[0])[1]
    return None


def latest_chronological_string(
    records: list[tuple[int, dict[str, Any]]],
    key: str,
) -> str | None:
    candidates: list[tuple[tuple[float, int], str]] = []
    untimed_candidates: list[tuple[int, str]] = []
    for record_index, record in records:
        value = string_value(record.get(key))
        if value is None:
            continue
        timestamp = parse_timestamp(string_value(record.get("timestamp")))
        if timestamp is None:
            untimed_candidates.append((record_index, value))
        else:
            candidates.append(((timestamp.timestamp(), record_index), value))
    if candidates:
        return max(candidates, key=lambda candidate: candidate[0])[1]
    if untimed_candidates:
        return max(untimed_candidates, key=lambda candidate: candidate[0])[1]
    return None


def latest_chronological_message_string(
    records: list[tuple[int, dict[str, Any]]],
    key: str,
) -> str | None:
    enriched_records = [
        (
            record_index,
            {
                **record,
                key: string_value(dict_value(record.get("message")).get(key))
                or string_value(record.get(key)),
            },
        )
        for record_index, record in records
    ]
    return latest_chronological_string(enriched_records, key)


def parsed_timestamps(
    records: list[tuple[int, dict[str, Any]]],
) -> list[datetime]:
    return [
        timestamp
        for _, record in records
        if (timestamp := parse_timestamp(string_value(record.get("timestamp")))) is not None
    ]


def timestamp_key(timestamp: datetime) -> float:
    return timestamp.timestamp()


def transition_count(
    records: list[tuple[int, dict[str, Any]]],
    key: str,
) -> int:
    previous: str | None = None
    transitions = 0
    for _, record in records:
        value = string_value(record.get(key))
        if value is None:
            continue
        if previous is not None and value != previous:
            transitions += 1
        previous = value
    return transitions
