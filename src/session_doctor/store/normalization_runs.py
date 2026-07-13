from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
from pydantic import BaseModel

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    CommandRun,
    FileActivity,
    Message,
    ModelUsage,
    ParseWarning,
    RawEvent,
    Session,
    SessionSource,
    ToolCall,
    ToolResult,
)

from .connection import read_connection, transaction, write_connection

NORMALIZATION_VERSION = "normalization-v2"
NORMALIZATION_CONFIGURATION_HASH = stable_id(
    "normalization-configuration",
    NORMALIZATION_VERSION,
    "default",
)


@dataclass(frozen=True)
class NormalizationRun:
    normalization_run_id: str
    bundle_content_id: str
    snapshot_bundle_id: str
    adapter_name: str
    adapter_version: str
    normalization_version: str
    configuration_hash: str


@dataclass(frozen=True)
class NormalizationCoverage:
    snapshot_bundle_id: str
    status: str
    current_normalization_run_id: str | None
    selected_normalization_run_id: str | None
    available_normalization_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class StoredNormalization:
    run: NormalizationRun
    source: SessionSource
    bundle: ParsedSessionBundle


class NormalizationConflictError(RuntimeError):
    pass


def normalization_identity(
    bundle_content_id: str,
    adapter_name: str,
    adapter_version: str,
    normalization_version: str = NORMALIZATION_VERSION,
    configuration_hash: str = NORMALIZATION_CONFIGURATION_HASH,
) -> str:
    return stable_id(
        bundle_content_id,
        adapter_name,
        adapter_version,
        normalization_version,
        configuration_hash,
    )


def persist_normalization(
    database_path: Path,
    snapshot_bundle_id: str,
    source: SessionSource,
    bundle: ParsedSessionBundle,
    *,
    adapter_version: str,
    normalization_version: str = NORMALIZATION_VERSION,
    configuration_hash: str = NORMALIZATION_CONFIGURATION_HASH,
) -> NormalizationRun:
    with write_connection(database_path) as connection, transaction(connection):
        return persist_normalization_rows(
            connection,
            snapshot_bundle_id,
            source,
            bundle,
            adapter_version,
            normalization_version,
            configuration_hash,
        )


def persist_normalization_rows(
    connection: duckdb.DuckDBPyConnection,
    snapshot_bundle_id: str,
    source: SessionSource,
    bundle: ParsedSessionBundle,
    adapter_version: str,
    normalization_version: str = NORMALIZATION_VERSION,
    configuration_hash: str = NORMALIZATION_CONFIGURATION_HASH,
) -> NormalizationRun:
    bundle_row = connection.execute(
        """
        SELECT bundle_content_id, agent_name
        FROM snapshot_bundles WHERE snapshot_bundle_id = ?
        """,
        [snapshot_bundle_id],
    ).fetchone()
    if bundle_row is None:
        raise ValueError(f"Snapshot bundle not found: {snapshot_bundle_id}")
    bundle_content_id, stored_agent_name = str(bundle_row[0]), str(bundle_row[1])
    adapter_name = source.agent_name.value
    if adapter_name != stored_agent_name:
        raise NormalizationConflictError("adapter does not own snapshot bundle")
    run_id = normalization_identity(
        bundle_content_id,
        adapter_name,
        adapter_version,
        normalization_version,
        configuration_hash,
    )
    connection.execute(
        """
        INSERT INTO normalization_runs (
            normalization_run_id, bundle_content_id, adapter_name,
            adapter_version, normalization_version, configuration_hash
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [
            run_id,
            bundle_content_id,
            adapter_name,
            adapter_version,
            normalization_version,
            configuration_hash,
        ],
    )
    stored_run = connection.execute(
        """
        SELECT bundle_content_id, adapter_name, adapter_version,
            normalization_version, configuration_hash
        FROM normalization_runs WHERE normalization_run_id = ?
        """,
        [run_id],
    ).fetchone()
    expected_run = (
        bundle_content_id,
        adapter_name,
        adapter_version,
        normalization_version,
        configuration_hash,
    )
    if stored_run != expected_run:
        raise NormalizationConflictError("normalization identity collision")
    connection.execute(
        """
        INSERT INTO normalization_run_bundles (
            normalization_run_id, snapshot_bundle_id
        ) VALUES (?, ?)
        ON CONFLICT DO NOTHING
        """,
        [run_id, snapshot_bundle_id],
    )
    rows = normalized_entity_rows(source, bundle)
    if rows:
        connection.executemany(
            """
            INSERT INTO normalized_entities (
                normalization_run_id, entity_kind, entity_id,
                entity_order, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [(run_id, *row) for row in rows],
        )
    stored_rows = connection.execute(
        """
        SELECT entity_kind, entity_id, entity_order, payload_json
        FROM normalized_entities WHERE normalization_run_id = ?
        ORDER BY entity_kind, entity_order, entity_id
        """,
        [run_id],
    ).fetchall()
    if tuple(stored_rows) != tuple(sorted(rows, key=lambda row: (row[0], row[2], row[1]))):
        raise NormalizationConflictError(
            "normalization replay differs for an existing semantic identity"
        )
    return NormalizationRun(
        normalization_run_id=run_id,
        bundle_content_id=bundle_content_id,
        snapshot_bundle_id=snapshot_bundle_id,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        normalization_version=normalization_version,
        configuration_hash=configuration_hash,
    )


def normalized_entity_rows(
    source: SessionSource, bundle: ParsedSessionBundle
) -> tuple[tuple[str, str, int, str], ...]:
    collections: tuple[tuple[str, tuple[BaseModel, ...]], ...] = (
        ("session_source", (source,)),
        ("session", tuple([bundle.session] if bundle.session is not None else [])),
        ("raw_event", tuple(bundle.raw_events)),
        ("message", tuple(bundle.messages)),
        ("tool_call", tuple(bundle.tool_calls)),
        ("tool_result", tuple(bundle.tool_results)),
        ("command_run", tuple(bundle.command_runs)),
        ("file_activity", tuple(bundle.file_activities)),
        ("model_usage", tuple(bundle.model_usage)),
        ("parse_warning", tuple(bundle.parse_warnings)),
    )
    rows: list[tuple[str, str, int, str]] = []
    for entity_kind, entities in collections:
        for entity_order, entity in enumerate(entities):
            entity_id = entity_identifier(entity_kind, entity)
            rows.append(
                (
                    entity_kind,
                    entity_id,
                    entity_order,
                    canonical_model_json(entity),
                )
            )
    return tuple(rows)


def canonical_model_json(entity: BaseModel) -> str:
    return json.dumps(
        entity.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def entity_identifier(entity_kind: str, entity: BaseModel) -> str:
    identifier_fields = {
        "session_source": "source_id",
        "session": "session_id",
        "raw_event": "event_id",
        "message": "message_id",
        "tool_call": "tool_call_id",
        "tool_result": "tool_result_id",
        "command_run": "command_run_id",
        "file_activity": "file_activity_id",
        "model_usage": "model_usage_id",
        "parse_warning": "warning_id",
    }
    field_name = identifier_fields[entity_kind]
    identifier = getattr(entity, field_name, None)
    if not isinstance(identifier, str) or not identifier:
        raise NormalizationConflictError(f"missing {field_name} for {entity_kind}")
    return identifier


def normalization_coverage(
    database_path: Path,
    snapshot_bundle_id: str,
    *,
    adapter_name: str,
    adapter_version: str,
    normalization_version: str = NORMALIZATION_VERSION,
    configuration_hash: str = NORMALIZATION_CONFIGURATION_HASH,
) -> NormalizationCoverage:
    with read_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT r.normalization_run_id, r.adapter_name, r.adapter_version,
                r.normalization_version, r.configuration_hash
            FROM normalization_run_bundles AS b
            JOIN normalization_runs AS r USING (normalization_run_id)
            WHERE b.snapshot_bundle_id = ?
            ORDER BY r.adapter_version DESC, r.normalization_version DESC,
                r.configuration_hash, r.normalization_run_id
            """,
            [snapshot_bundle_id],
        ).fetchall()
    ordered_rows = tuple(
        sorted(
            rows,
            key=lambda row: (
                parser_version_key(str(row[2])) or (-1,),
                str(row[0]),
            ),
            reverse=True,
        )
    )
    available = tuple(str(row[0]) for row in ordered_rows)
    current = next(
        (
            str(row[0])
            for row in ordered_rows
            if row[1:]
            == (
                adapter_name,
                adapter_version,
                normalization_version,
                configuration_hash,
            )
        ),
        None,
    )
    current_version_key = parser_version_key(adapter_version)
    compatible = tuple(
        row
        for row in ordered_rows
        if row[1] == adapter_name
        and row[3] == normalization_version
        and row[4] == configuration_hash
        and versions_compatible(
            parser_version_key(str(row[2])),
            current_version_key,
            str(row[2]),
            adapter_version,
        )
    )
    selected = current or (str(compatible[0][0]) if compatible else None)
    status = "current" if current is not None else "stale" if available else "missing"
    return NormalizationCoverage(
        snapshot_bundle_id=snapshot_bundle_id,
        status=status,
        current_normalization_run_id=current,
        selected_normalization_run_id=selected,
        available_normalization_run_ids=available,
    )


def parser_version_key(value: str) -> tuple[int, ...] | None:
    if re.fullmatch(r"\d+(?:\.\d+)*", value) is None:
        return None
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def versions_compatible(
    candidate: tuple[int, ...] | None,
    current: tuple[int, ...] | None,
    candidate_raw: str,
    current_raw: str,
) -> bool:
    if candidate is None or current is None:
        return candidate_raw == current_raw
    return candidate[0] == current[0] and candidate <= current


def load_normalization(
    database_path: Path, normalization_run_id: str
) -> StoredNormalization | None:
    with read_connection(database_path) as connection:
        run_row = connection.execute(
            """
            SELECT r.bundle_content_id, b.snapshot_bundle_id, r.adapter_name,
                r.adapter_version, r.normalization_version, r.configuration_hash
            FROM normalization_runs AS r
            JOIN normalization_run_bundles AS b USING (normalization_run_id)
            WHERE r.normalization_run_id = ?
            ORDER BY b.snapshot_bundle_id LIMIT 1
            """,
            [normalization_run_id],
        ).fetchone()
        if run_row is None:
            return None
        rows = connection.execute(
            """
            SELECT entity_kind, payload_json FROM normalized_entities
            WHERE normalization_run_id = ?
            ORDER BY entity_kind, entity_order, entity_id
            """,
            [normalization_run_id],
        ).fetchall()
    by_kind: dict[str, list[str]] = {}
    for entity_kind, payload_json in rows:
        by_kind.setdefault(str(entity_kind), []).append(str(payload_json))
    source_payloads = by_kind.get("session_source", [])
    if len(source_payloads) != 1:
        raise NormalizationConflictError("normalization source descriptor is missing")
    source = SessionSource.model_validate_json(source_payloads[0])
    session_payloads = by_kind.get("session", [])
    bundle = ParsedSessionBundle(
        session=(Session.model_validate_json(session_payloads[0]) if session_payloads else None),
        raw_events=parse_entities(RawEvent, by_kind.get("raw_event", [])),
        messages=parse_entities(Message, by_kind.get("message", [])),
        tool_calls=parse_entities(ToolCall, by_kind.get("tool_call", [])),
        tool_results=parse_entities(ToolResult, by_kind.get("tool_result", [])),
        command_runs=parse_entities(CommandRun, by_kind.get("command_run", [])),
        file_activities=parse_entities(FileActivity, by_kind.get("file_activity", [])),
        model_usage=parse_entities(ModelUsage, by_kind.get("model_usage", [])),
        parse_warnings=parse_entities(ParseWarning, by_kind.get("parse_warning", [])),
    )
    run = NormalizationRun(
        normalization_run_id=normalization_run_id,
        bundle_content_id=str(run_row[0]),
        snapshot_bundle_id=str(run_row[1]),
        adapter_name=str(run_row[2]),
        adapter_version=str(run_row[3]),
        normalization_version=str(run_row[4]),
        configuration_hash=str(run_row[5]),
    )
    return StoredNormalization(run=run, source=source, bundle=bundle)


def parse_entities(model: type[BaseModel], payloads: list[str]) -> list:
    return [model.model_validate_json(payload) for payload in payloads]
