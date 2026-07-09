from __future__ import annotations

import duckdb

from session_doctor.normalization import canonical_command_identity, canonical_file_identity

SCHEMA_VERSION = 3

TABLE_NAMES = (
    "schema_migrations",
    "session_sources",
    "sessions",
    "raw_events",
    "messages",
    "tool_calls",
    "tool_results",
    "command_runs",
    "file_activities",
    "model_usage",
    "parse_warnings",
    "analysis_runs",
    "message_features",
    "session_features",
    "session_classifications",
    "graph_nodes",
    "graph_edges",
)


def apply_migrations(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )

    for statement in CREATE_TABLE_STATEMENTS:
        connection.execute(statement)

    migrate_canonical_identity_columns(connection)

    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        [SCHEMA_VERSION],
    )


CREATE_TABLE_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS session_sources (
        source_id VARCHAR PRIMARY KEY,
        agent_name VARCHAR NOT NULL,
        source_path VARCHAR NOT NULL,
        source_kind VARCHAR NOT NULL,
        discovered_at TIMESTAMP,
        native_session_id VARCHAR,
        parent_source_id VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        agent_name VARCHAR NOT NULL,
        native_session_id VARCHAR,
        parent_session_id VARCHAR,
        started_at TIMESTAMP,
        ended_at TIMESTAMP,
        cwd VARCHAR,
        project_path VARCHAR,
        agent_version VARCHAR,
        model_provider VARCHAR,
        model VARCHAR,
        is_sidechain BOOLEAN NOT NULL DEFAULT false,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_events (
        event_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        agent_name VARCHAR NOT NULL,
        record_index INTEGER NOT NULL,
        native_event_type VARCHAR,
        native_event_id VARCHAR,
        native_parent_id VARCHAR,
        timestamp TIMESTAMP,
        payload_hash VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        role VARCHAR NOT NULL,
        source_event_id VARCHAR,
        native_message_id VARCHAR,
        parent_message_id VARCHAR,
        timestamp TIMESTAMP,
        text VARCHAR,
        text_hash VARCHAR,
        text_length INTEGER NOT NULL DEFAULT 0,
        content_block_types_json VARCHAR NOT NULL DEFAULT '[]',
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        tool_call_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        native_tool_call_id VARCHAR,
        name VARCHAR NOT NULL,
        timestamp TIMESTAMP,
        arguments_hash VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_results (
        tool_result_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        tool_call_id VARCHAR,
        source_event_id VARCHAR,
        native_tool_call_id VARCHAR,
        timestamp TIMESTAMP,
        is_error BOOLEAN,
        output_hash VARCHAR,
        output_length INTEGER,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS command_runs (
        command_run_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        tool_call_id VARCHAR,
        command VARCHAR NOT NULL,
        command_identity_hash VARCHAR NOT NULL,
        command_display VARCHAR NOT NULL,
        command_normalization VARCHAR NOT NULL,
        cwd VARCHAR,
        started_at TIMESTAMP,
        ended_at TIMESTAMP,
        exit_code INTEGER,
        stdout_hash VARCHAR,
        stderr_hash VARCHAR,
        output_length INTEGER,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS file_activities (
        file_activity_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        path VARCHAR NOT NULL,
        normalized_path VARCHAR NOT NULL,
        canonical_path VARCHAR,
        project_relative_path VARCHAR,
        path_resolution VARCHAR NOT NULL,
        operation VARCHAR NOT NULL,
        timestamp TIMESTAMP,
        content_hash VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_usage (
        model_usage_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        timestamp TIMESTAMP,
        provider VARCHAR,
        model VARCHAR,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cache_read_tokens INTEGER,
        cache_write_tokens INTEGER,
        total_tokens INTEGER,
        cost DECIMAL(18, 8),
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parse_warnings (
        warning_id VARCHAR PRIMARY KEY,
        source_id VARCHAR NOT NULL,
        record_index INTEGER,
        severity VARCHAR NOT NULL,
        message VARCHAR NOT NULL,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        analysis_run_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        analyzer_version VARCHAR NOT NULL,
        artifact_path VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS message_features (
        message_feature_id VARCHAR PRIMARY KEY,
        analysis_run_id VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        message_id VARCHAR NOT NULL,
        source_event_id VARCHAR,
        feature_name VARCHAR NOT NULL,
        feature_value VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        evidence_json VARCHAR NOT NULL DEFAULT '{}',
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_features (
        session_feature_id VARCHAR PRIMARY KEY,
        analysis_run_id VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        feature_name VARCHAR NOT NULL,
        feature_value VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        evidence_json VARCHAR NOT NULL DEFAULT '{}',
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_classifications (
        session_classification_id VARCHAR PRIMARY KEY,
        analysis_run_id VARCHAR NOT NULL,
        session_id VARCHAR NOT NULL,
        label VARCHAR NOT NULL,
        score DOUBLE NOT NULL,
        confidence DOUBLE NOT NULL,
        evidence_event_ids_json VARCHAR NOT NULL DEFAULT '[]',
        evidence_summary VARCHAR NOT NULL,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_nodes (
        node_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        node_type VARCHAR NOT NULL,
        label VARCHAR NOT NULL,
        source_event_id VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_edges (
        edge_id VARCHAR PRIMARY KEY,
        session_id VARCHAR NOT NULL,
        source_node_id VARCHAR NOT NULL,
        target_node_id VARCHAR NOT NULL,
        edge_type VARCHAR NOT NULL,
        confidence DOUBLE NOT NULL,
        source_event_id VARCHAR,
        metadata_json VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
)


def migrate_canonical_identity_columns(connection: duckdb.DuckDBPyConnection) -> None:
    add_column_if_missing(connection, "command_runs", "command_identity_hash", "VARCHAR")
    add_column_if_missing(connection, "command_runs", "command_display", "VARCHAR")
    add_column_if_missing(connection, "command_runs", "command_normalization", "VARCHAR")
    add_column_if_missing(connection, "file_activities", "normalized_path", "VARCHAR")
    add_column_if_missing(connection, "file_activities", "canonical_path", "VARCHAR")
    add_column_if_missing(connection, "file_activities", "project_relative_path", "VARCHAR")
    add_column_if_missing(connection, "file_activities", "path_resolution", "VARCHAR")

    command_rows = connection.execute(
        """
        SELECT command_run_id, command
        FROM command_runs
        WHERE command_identity_hash IS NULL
           OR command_display IS NULL
           OR command_normalization IS NULL
        """
    ).fetchall()
    for command_run_id, command in command_rows:
        identity = canonical_command_identity(str(command))
        connection.execute(
            """
            UPDATE command_runs
            SET command_identity_hash = ?, command_display = ?, command_normalization = ?
            WHERE command_run_id = ?
            """,
            [identity.identity_hash, identity.display, identity.normalization, command_run_id],
        )

    file_rows = connection.execute(
        """
        SELECT f.file_activity_id, f.path, s.cwd, s.project_path
        FROM file_activities AS f
        JOIN sessions AS s ON s.session_id = f.session_id
        WHERE f.normalized_path IS NULL OR f.path_resolution IS NULL
        """
    ).fetchall()
    for file_activity_id, path, cwd, project_path in file_rows:
        identity = canonical_file_identity(
            str(path),
            cwd=str(cwd) if cwd else None,
            project_path=str(project_path) if project_path else None,
        )
        connection.execute(
            """
            UPDATE file_activities
            SET normalized_path = ?, canonical_path = ?, project_relative_path = ?,
                path_resolution = ?
            WHERE file_activity_id = ?
            """,
            [
                identity.normalized_path,
                identity.canonical_path,
                identity.project_relative_path,
                identity.resolution,
                file_activity_id,
            ],
        )


def add_column_if_missing(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    existing = connection.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = ? AND column_name = ?
        """,
        [table_name, column_name],
    ).fetchone()
    if existing is None:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
