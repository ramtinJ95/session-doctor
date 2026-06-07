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


def insert_parsed_bundle(
    database_path: Path,
    source: SessionSource,
    bundle: ParsedSessionBundle,
) -> None:
    with write_connection(database_path) as connection, transaction(connection):
        delete_source_records(connection, source.source_id)
        insert_session_source(connection, source, bundle)
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
