from __future__ import annotations

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

from .connection import transaction, write_connection
from .json_values import duckdb_value, metadata_json
from .row_mappers import (
    analysis_run_rows,
    command_run_rows,
    file_activity_rows,
    message_feature_rows,
    message_rows,
    model_usage_rows,
    parse_warning_rows,
    raw_event_rows,
    session_classification_rows,
    session_feature_rows,
    session_rows,
    tool_call_rows,
    tool_result_rows,
)
from .snapshots import CapturedBundle, CapturedSource


def insert_parsed_bundle(
    database_path: Path,
    source: SessionSource,
    bundle: ParsedSessionBundle,
    captured_source: CapturedSource,
    captured_bundle: CapturedBundle,
) -> None:
    with write_connection(database_path) as connection, transaction(connection):
        provenance = connection.execute(
            """
            SELECT b.bundle_content_id, b.native_session_identity,
                b.native_identity_status, b.native_bundle_capture_sequence
            FROM source_snapshots AS s
            JOIN snapshot_bundle_members AS m
              ON m.snapshot_id = s.snapshot_id
             AND m.logical_source_id = s.logical_source_id
            JOIN snapshot_bundles AS b
              ON b.snapshot_bundle_id = m.snapshot_bundle_id
             AND b.primary_snapshot_id = s.snapshot_id
             AND b.agent_name = s.agent_name
            WHERE s.snapshot_id = ?
              AND s.source_id = ?
              AND s.logical_source_id = ?
              AND m.snapshot_bundle_id = ?
              AND s.agent_name = ?
              AND s.source_kind = ?
              AND s.source_path = ?
              AND s.discovered_at IS NOT DISTINCT FROM ?
              AND s.native_session_id IS NOT DISTINCT FROM ?
              AND s.parent_source_id IS NOT DISTINCT FROM ?
              AND s.source_metadata_json = ?
            """,
            [
                captured_source.snapshot_id,
                source.source_id,
                captured_source.logical_source_id,
                captured_bundle.snapshot_bundle_id,
                source.agent_name.value,
                source.source_kind.value,
                source.source_path,
                source.discovered_at.isoformat() if source.discovered_at else None,
                source.native_session_id,
                source.parent_source_id,
                metadata_json(source.metadata),
            ],
        ).fetchone()
        expected_native_session_identity = (
            bundle.session.native_session_id
            if bundle.session and bundle.session.native_session_id
            else source.native_session_id or source.source_id
        )
        if provenance != (
            captured_bundle.bundle_content_id,
            expected_native_session_identity,
            captured_bundle.native_identity_status,
            captured_bundle.capture_sequence,
        ) or (
            captured_bundle.native_session_identity != expected_native_session_identity
            or captured_bundle.native_identity_status != "observed"
        ):
            raise CaptureProvenanceError(source.source_id)
        latest = connection.execute(
            """
            SELECT snapshot_id
            FROM source_snapshots
            WHERE logical_source_id = ?
            ORDER BY capture_sequence DESC
            LIMIT 1
            """,
            [captured_source.logical_source_id],
        ).fetchone()
        if latest is None or str(latest[0]) != captured_source.snapshot_id:
            raise StaleCaptureError(captured_source.snapshot_id)
        delete_source_records(connection, source.source_id)
        insert_session_source(connection, source, bundle, captured_source, captured_bundle)
        if bundle.session:
            insert_rows(connection, "sessions", session_rows(bundle))
        insert_rows(connection, "raw_events", raw_event_rows(bundle))
        insert_rows(connection, "messages", message_rows(bundle))
        insert_rows(connection, "tool_calls", tool_call_rows(bundle))
        insert_rows(connection, "tool_results", tool_result_rows(bundle))
        insert_rows(connection, "command_runs", command_run_rows(bundle))
        insert_rows(connection, "file_activities", file_activity_rows(bundle))
        insert_rows(connection, "model_usage", model_usage_rows(bundle))
        insert_rows(connection, "parse_warnings", parse_warning_rows(bundle))


def insert_untracked_parsed_bundle(
    database_path: Path,
    source: SessionSource,
    bundle: ParsedSessionBundle,
) -> None:
    with write_connection(database_path) as connection, transaction(connection):
        delete_source_records(connection, source.source_id)
        insert_session_source(connection, source, bundle, None, None)
        if bundle.session:
            insert_rows(connection, "sessions", session_rows(bundle))
        insert_rows(connection, "raw_events", raw_event_rows(bundle))
        insert_rows(connection, "messages", message_rows(bundle))
        insert_rows(connection, "tool_calls", tool_call_rows(bundle))
        insert_rows(connection, "tool_results", tool_result_rows(bundle))
        insert_rows(connection, "command_runs", command_run_rows(bundle))
        insert_rows(connection, "file_activities", file_activity_rows(bundle))
        insert_rows(connection, "model_usage", model_usage_rows(bundle))
        insert_rows(connection, "parse_warnings", parse_warning_rows(bundle))


class StaleCaptureError(RuntimeError):
    def __init__(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id
        super().__init__(f"capture {snapshot_id} is no longer the latest source snapshot")


class CaptureProvenanceError(RuntimeError):
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        super().__init__(f"capture provenance does not belong to source {source_id}")


def replace_analysis_rows(
    database_path: Path,
    analysis_run: AnalysisRun,
    message_features: list[MessageFeature],
    session_features: list[SessionFeature],
    session_classifications: list[SessionClassification],
) -> None:
    with write_connection(database_path) as connection, transaction(connection):
        delete_analysis_records(connection, analysis_run.session_id)
        insert_rows(connection, "analysis_runs", analysis_run_rows(analysis_run))
        insert_rows(connection, "message_features", message_feature_rows(message_features))
        insert_rows(connection, "session_features", session_feature_rows(session_features))
        insert_rows(
            connection,
            "session_classifications",
            session_classification_rows(session_classifications),
        )


def delete_source_records(connection: duckdb.DuckDBPyConnection, source_id: str) -> None:
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
        ):
            connection.execute(
                f"DELETE FROM {table_name} WHERE session_id = ?",
                [session_id],
            )
    connection.execute("DELETE FROM parse_warnings WHERE source_id = ?", [source_id])
    connection.execute("DELETE FROM raw_events WHERE source_id = ?", [source_id])
    connection.execute("DELETE FROM sessions WHERE source_id = ?", [source_id])
    connection.execute("DELETE FROM session_sources WHERE source_id = ?", [source_id])


def delete_analysis_records(connection: duckdb.DuckDBPyConnection, session_id: str) -> None:
    for table_name in (
        "message_features",
        "session_features",
        "session_classifications",
        "analysis_runs",
    ):
        connection.execute(f"DELETE FROM {table_name} WHERE session_id = ?", [session_id])


def insert_session_source(
    connection: duckdb.DuckDBPyConnection,
    source: SessionSource,
    bundle: ParsedSessionBundle,
    captured_source: CapturedSource | None,
    captured_bundle: CapturedBundle | None,
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
            snapshot_id,
            snapshot_bundle_id,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            source.source_id,
            source.agent_name.value,
            source.source_path,
            source.source_kind.value,
            duckdb_value(source.discovered_at),
            native_session_id,
            source.parent_source_id,
            captured_source.snapshot_id if captured_source else None,
            captured_bundle.snapshot_bundle_id if captured_bundle else None,
            metadata_json(source.metadata),
        ],
    )


def insert_rows(
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
