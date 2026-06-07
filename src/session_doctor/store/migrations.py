from __future__ import annotations

import duckdb

SCHEMA_VERSION = 2

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
