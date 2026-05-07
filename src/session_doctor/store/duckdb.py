from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    AnalysisRun,
    MessageFeature,
    SessionClassification,
    SessionFeature,
    SessionSource,
)

from .migrations import TABLE_NAMES, apply_migrations


@dataclass(frozen=True)
class StoreInfo:
    database_path: Path
    exists: bool
    schema_version: int | None
    tables: tuple[str, ...]


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    agent_name: str
    started_at: str | None
    ended_at: str | None
    cwd: str | None
    project_path: str | None
    source_path: str | None
    message_count: int
    response_item_message_count: int
    event_msg_fallback_count: int
    command_count: int
    warning_count: int


class DuckDBStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser()

    def initialize(self) -> StoreInfo:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.database_path)) as connection:
            apply_migrations(connection)
        return self.info()

    def insert_parsed_bundle(
        self,
        source: SessionSource,
        bundle: ParsedSessionBundle,
    ) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.database_path)) as connection:
            apply_migrations(connection)
            connection.execute("BEGIN TRANSACTION")
            try:
                self._delete_source_records(connection, source.source_id)
                self._insert_session_source(connection, source, bundle)
                if bundle.session:
                    self._insert_rows(connection, "sessions", session_rows(bundle))
                self._insert_rows(connection, "raw_events", raw_event_rows(bundle))
                self._insert_rows(connection, "messages", message_rows(bundle))
                self._insert_rows(connection, "tool_calls", tool_call_rows(bundle))
                self._insert_rows(connection, "tool_results", tool_result_rows(bundle))
                self._insert_rows(connection, "command_runs", command_run_rows(bundle))
                self._insert_rows(connection, "file_activities", file_activity_rows(bundle))
                self._insert_rows(connection, "model_usage", model_usage_rows(bundle))
                self._insert_rows(connection, "parse_warnings", parse_warning_rows(bundle))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def replace_analysis_rows(
        self,
        analysis_run: AnalysisRun,
        message_features: list[MessageFeature],
        session_features: list[SessionFeature],
        session_classifications: list[SessionClassification],
    ) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.database_path)) as connection:
            apply_migrations(connection)
            connection.execute("BEGIN TRANSACTION")
            try:
                self._delete_analysis_records(connection, analysis_run.session_id)
                self._insert_rows(connection, "analysis_runs", analysis_run_rows(analysis_run))
                self._insert_rows(
                    connection,
                    "message_features",
                    message_feature_rows(message_features),
                )
                self._insert_rows(
                    connection,
                    "session_features",
                    session_feature_rows(session_features),
                )
                self._insert_rows(
                    connection,
                    "session_classifications",
                    session_classification_rows(session_classifications),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def table_count(self, table_name: str) -> int:
        if table_name not in TABLE_NAMES:
            msg = f"Unknown table: {table_name}"
            raise ValueError(msg)
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        return int(row[0]) if row else 0

    def list_session_summaries(self) -> tuple[SessionSummary, ...]:
        if not self.database_path.exists():
            return ()
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT
                    s.session_id,
                    s.agent_name,
                    CAST(s.started_at AS VARCHAR),
                    CAST(s.ended_at AS VARCHAR),
                    s.cwd,
                    s.project_path,
                    ss.source_path,
                    COUNT(DISTINCT m.message_id) AS message_count,
                    COUNT(DISTINCT c.command_run_id) AS command_count,
                    COUNT(DISTINCT w.warning_id) AS warning_count
                FROM sessions s
                LEFT JOIN session_sources ss ON ss.source_id = s.source_id
                LEFT JOIN messages m ON m.session_id = s.session_id
                LEFT JOIN command_runs c ON c.session_id = s.session_id
                LEFT JOIN parse_warnings w ON w.source_id = s.source_id
                GROUP BY
                    s.session_id,
                    s.agent_name,
                    s.started_at,
                    s.ended_at,
                    s.cwd,
                    s.project_path,
                    ss.source_path
                ORDER BY s.started_at NULLS LAST, s.session_id
                """
            ).fetchall()
            message_source_counts = self._message_source_counts(connection)

        summaries: list[SessionSummary] = []
        for row in rows:
            session_counts = message_source_counts.get(row[0], {})
            summaries.append(
                SessionSummary(
                    session_id=str(row[0]),
                    agent_name=str(row[1]),
                    started_at=row[2],
                    ended_at=row[3],
                    cwd=row[4],
                    project_path=row[5],
                    source_path=row[6],
                    message_count=int(row[7]),
                    response_item_message_count=session_counts.get("response_item", 0),
                    event_msg_fallback_count=session_counts.get("event_msg_fallback", 0),
                    command_count=int(row[8]),
                    warning_count=int(row[9]),
                )
            )
        return tuple(summaries)

    def info(self) -> StoreInfo:
        if not self.database_path.exists():
            return StoreInfo(
                database_path=self.database_path,
                exists=False,
                schema_version=None,
                tables=(),
            )

        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            tables = tuple(
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                    ORDER BY table_name
                    """
                ).fetchall()
            )
            schema_version = self._schema_version(connection, tables)

        return StoreInfo(
            database_path=self.database_path,
            exists=True,
            schema_version=schema_version,
            tables=tables,
        )

    @staticmethod
    def _schema_version(
        connection: duckdb.DuckDBPyConnection,
        tables: tuple[str, ...],
    ) -> int | None:
        if "schema_migrations" not in tables:
            return None
        row = connection.execute(
            "SELECT MAX(version) FROM schema_migrations",
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    @staticmethod
    def _delete_source_records(
        connection: duckdb.DuckDBPyConnection,
        source_id: str,
    ) -> None:
        session_rows_for_source = connection.execute(
            "SELECT session_id FROM sessions WHERE source_id = ?",
            [source_id],
        ).fetchall()
        session_ids = [row[0] for row in session_rows_for_source]
        for session_id in session_ids:
            for table_name in (
                "messages",
                "tool_calls",
                "tool_results",
                "command_runs",
                "file_activities",
                "model_usage",
                "message_features",
                "session_features",
                "session_classifications",
                "analysis_runs",
                "graph_nodes",
                "graph_edges",
            ):
                connection.execute(
                    f"DELETE FROM {table_name} WHERE session_id = ?",
                    [session_id],
                )
        connection.execute("DELETE FROM parse_warnings WHERE source_id = ?", [source_id])
        connection.execute("DELETE FROM raw_events WHERE source_id = ?", [source_id])
        connection.execute("DELETE FROM sessions WHERE source_id = ?", [source_id])
        connection.execute("DELETE FROM session_sources WHERE source_id = ?", [source_id])

    @staticmethod
    def _delete_analysis_records(
        connection: duckdb.DuckDBPyConnection,
        session_id: str,
    ) -> None:
        for table_name in (
            "message_features",
            "session_features",
            "session_classifications",
            "analysis_runs",
        ):
            connection.execute(f"DELETE FROM {table_name} WHERE session_id = ?", [session_id])

    @staticmethod
    def _insert_session_source(
        connection: duckdb.DuckDBPyConnection,
        source: SessionSource,
        bundle: ParsedSessionBundle,
    ) -> None:
        native_session_id = source.native_session_id
        if bundle.session and bundle.session.native_session_id:
            native_session_id = bundle.session.native_session_id
        connection.execute(
            """
            INSERT INTO session_sources (
                source_id,
                agent_name,
                source_path,
                source_kind,
                discovered_at,
                native_session_id,
                parent_source_id,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source.source_id,
                source.agent_name.value,
                source.source_path,
                source.source_kind.value,
                duckdb_value(source.discovered_at),
                native_session_id,
                source.parent_source_id,
                metadata_json(source.metadata),
            ],
        )

    @staticmethod
    def _insert_rows(
        connection: duckdb.DuckDBPyConnection,
        table_name: str,
        rows: list[dict[str, Any]],
    ) -> None:
        if not rows:
            return
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        column_names = ", ".join(columns)
        values = [[duckdb_value(row[column]) for column in columns] for row in rows]
        connection.executemany(
            f"INSERT INTO {table_name} ({column_names}) VALUES ({placeholders})",
            values,
        )

    @staticmethod
    def _message_source_counts(
        connection: duckdb.DuckDBPyConnection,
    ) -> dict[str, dict[str, int]]:
        rows = connection.execute(
            "SELECT session_id, metadata_json FROM messages",
        ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for session_id, metadata_payload in rows:
            metadata = parse_metadata(metadata_payload)
            source = metadata.get("codex_message_source")
            if not isinstance(source, str):
                continue
            session_counts = counts.setdefault(str(session_id), {})
            session_counts[source] = session_counts.get(source, 0) + 1
        return counts


def session_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    if bundle.session is None:
        return []
    session = bundle.session
    return [
        {
            "session_id": session.session_id,
            "source_id": session.source_id,
            "agent_name": session.agent_name.value,
            "native_session_id": session.native_session_id,
            "parent_session_id": session.parent_session_id,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "cwd": session.cwd,
            "project_path": session.project_path,
            "agent_version": session.agent_version,
            "model_provider": session.model_provider,
            "model": session.model,
            "is_sidechain": session.is_sidechain,
            "metadata_json": metadata_json(session.metadata),
        }
    ]


def raw_event_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event.event_id,
            "source_id": event.source_id,
            "agent_name": event.agent_name.value,
            "record_index": event.record_index,
            "native_event_type": event.native_event_type,
            "native_event_id": event.native_event_id,
            "native_parent_id": event.native_parent_id,
            "timestamp": event.timestamp,
            "payload_hash": event.payload_hash,
            "metadata_json": metadata_json(event.metadata),
        }
        for event in bundle.raw_events
    ]


def message_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "message_id": message.message_id,
            "session_id": message.session_id,
            "role": message.role.value,
            "source_event_id": message.source_event_id,
            "native_message_id": message.native_message_id,
            "parent_message_id": message.parent_message_id,
            "timestamp": message.timestamp,
            "text": message.text,
            "text_hash": message.text_hash,
            "text_length": message.text_length,
            "content_block_types_json": json.dumps(message.content_block_types),
            "metadata_json": metadata_json(message.metadata),
        }
        for message in bundle.messages
    ]


def tool_call_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "tool_call_id": tool_call.tool_call_id,
            "session_id": tool_call.session_id,
            "source_event_id": tool_call.source_event_id,
            "native_tool_call_id": tool_call.native_tool_call_id,
            "name": tool_call.name,
            "timestamp": tool_call.timestamp,
            "arguments_hash": tool_call.arguments_hash,
            "metadata_json": metadata_json(tool_call.metadata),
        }
        for tool_call in bundle.tool_calls
    ]


def tool_result_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "tool_result_id": tool_result.tool_result_id,
            "session_id": tool_result.session_id,
            "tool_call_id": tool_result.tool_call_id,
            "source_event_id": tool_result.source_event_id,
            "native_tool_call_id": tool_result.native_tool_call_id,
            "timestamp": tool_result.timestamp,
            "is_error": tool_result.is_error,
            "output_hash": tool_result.output_hash,
            "output_length": tool_result.output_length,
            "metadata_json": metadata_json(tool_result.metadata),
        }
        for tool_result in bundle.tool_results
    ]


def command_run_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "command_run_id": command_run.command_run_id,
            "session_id": command_run.session_id,
            "source_event_id": command_run.source_event_id,
            "tool_call_id": command_run.tool_call_id,
            "command": command_run.command,
            "cwd": command_run.cwd,
            "started_at": command_run.started_at,
            "ended_at": command_run.ended_at,
            "exit_code": command_run.exit_code,
            "stdout_hash": command_run.stdout_hash,
            "stderr_hash": command_run.stderr_hash,
            "output_length": command_run.output_length,
            "metadata_json": metadata_json(command_run.metadata),
        }
        for command_run in bundle.command_runs
    ]


def file_activity_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "file_activity_id": file_activity.file_activity_id,
            "session_id": file_activity.session_id,
            "source_event_id": file_activity.source_event_id,
            "path": file_activity.path,
            "operation": file_activity.operation,
            "timestamp": file_activity.timestamp,
            "content_hash": file_activity.content_hash,
            "metadata_json": metadata_json(file_activity.metadata),
        }
        for file_activity in bundle.file_activities
    ]


def model_usage_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "model_usage_id": usage.model_usage_id,
            "session_id": usage.session_id,
            "source_event_id": usage.source_event_id,
            "timestamp": usage.timestamp,
            "provider": usage.provider,
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "total_tokens": usage.total_tokens,
            "cost": usage.cost,
            "metadata_json": metadata_json(usage.metadata),
        }
        for usage in bundle.model_usage
    ]


def parse_warning_rows(bundle: ParsedSessionBundle) -> list[dict[str, Any]]:
    return [
        {
            "warning_id": warning.warning_id,
            "source_id": warning.source_id,
            "record_index": warning.record_index,
            "severity": warning.severity,
            "message": warning.message,
            "metadata_json": metadata_json(warning.metadata),
        }
        for warning in bundle.parse_warnings
    ]


def analysis_run_rows(analysis_run: AnalysisRun) -> list[dict[str, Any]]:
    return [
        {
            "analysis_run_id": analysis_run.analysis_run_id,
            "session_id": analysis_run.session_id,
            "started_at": analysis_run.started_at,
            "completed_at": analysis_run.completed_at,
            "analyzer_version": analysis_run.analyzer_version,
            "artifact_path": analysis_run.artifact_path,
            "metadata_json": metadata_json(analysis_run.metadata),
        }
    ]


def message_feature_rows(features: list[MessageFeature]) -> list[dict[str, Any]]:
    return [
        {
            "message_feature_id": feature.message_feature_id,
            "analysis_run_id": feature.analysis_run_id,
            "session_id": feature.session_id,
            "message_id": feature.message_id,
            "source_event_id": feature.source_event_id,
            "feature_name": feature.feature_name,
            "feature_value": feature.feature_value,
            "score": feature.score,
            "evidence_json": metadata_json(feature.evidence),
            "metadata_json": metadata_json(feature.metadata),
        }
        for feature in features
    ]


def session_feature_rows(features: list[SessionFeature]) -> list[dict[str, Any]]:
    return [
        {
            "session_feature_id": feature.session_feature_id,
            "analysis_run_id": feature.analysis_run_id,
            "session_id": feature.session_id,
            "feature_name": feature.feature_name,
            "feature_value": feature.feature_value,
            "score": feature.score,
            "evidence_json": metadata_json(feature.evidence),
            "metadata_json": metadata_json(feature.metadata),
        }
        for feature in features
    ]


def session_classification_rows(
    classifications: list[SessionClassification],
) -> list[dict[str, Any]]:
    return [
        {
            "session_classification_id": classification.session_classification_id,
            "analysis_run_id": classification.analysis_run_id,
            "session_id": classification.session_id,
            "label": classification.label,
            "score": classification.score,
            "confidence": classification.confidence,
            "evidence_event_ids_json": json.dumps(classification.evidence_event_ids),
            "evidence_summary": classification.evidence_summary,
            "metadata_json": metadata_json(classification.metadata),
        }
        for classification in classifications
    ]


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
