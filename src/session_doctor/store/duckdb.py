from __future__ import annotations

from pathlib import Path

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    AnalysisRun,
    MessageFeature,
    SessionClassification,
    SessionFeature,
    SessionSource,
)

from .connection import initialize_database
from .json_values import duckdb_value, metadata_json, parse_metadata, parse_string_list
from .models import SessionSummary, StoreInfo
from .readers import (
    latest_schema_version,
    list_session_summaries,
    load_session_bundle,
    message_source_counts_by_session,
    store_info,
    table_count,
)
from .row_loaders import (
    load_command_runs,
    load_file_activities,
    load_messages,
    load_model_usage,
    load_parse_warnings,
    load_raw_events,
    load_session,
    load_tool_calls,
    load_tool_results,
)
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
from .writers import (
    delete_analysis_records,
    delete_source_records,
    insert_rows,
    insert_session_source,
)
from .writers import (
    insert_parsed_bundle as write_parsed_bundle,
)
from .writers import (
    replace_analysis_rows as write_analysis_rows,
)

__all__ = [
    "DuckDBStore",
    "SessionSummary",
    "StoreInfo",
    "analysis_run_rows",
    "command_run_rows",
    "duckdb_value",
    "file_activity_rows",
    "message_feature_rows",
    "message_rows",
    "metadata_json",
    "model_usage_rows",
    "parse_metadata",
    "parse_string_list",
    "parse_warning_rows",
    "raw_event_rows",
    "session_classification_rows",
    "session_feature_rows",
    "session_rows",
    "tool_call_rows",
    "tool_result_rows",
]


class DuckDBStore:
    _schema_version = staticmethod(latest_schema_version)
    _delete_source_records = staticmethod(delete_source_records)
    _delete_analysis_records = staticmethod(delete_analysis_records)
    _insert_session_source = staticmethod(insert_session_source)
    _insert_rows = staticmethod(insert_rows)
    _message_source_counts = staticmethod(message_source_counts_by_session)
    _load_session = staticmethod(load_session)
    _load_raw_events = staticmethod(load_raw_events)
    _load_messages = staticmethod(load_messages)
    _load_tool_calls = staticmethod(load_tool_calls)
    _load_tool_results = staticmethod(load_tool_results)
    _load_command_runs = staticmethod(load_command_runs)
    _load_file_activities = staticmethod(load_file_activities)
    _load_model_usage = staticmethod(load_model_usage)
    _load_parse_warnings = staticmethod(load_parse_warnings)

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser()

    def initialize(self) -> StoreInfo:
        initialize_database(self.database_path)
        return self.info()

    def insert_parsed_bundle(
        self,
        source: SessionSource,
        bundle: ParsedSessionBundle,
    ) -> None:
        write_parsed_bundle(self.database_path, source, bundle)

    def replace_analysis_rows(
        self,
        analysis_run: AnalysisRun,
        message_features: list[MessageFeature],
        session_features: list[SessionFeature],
        session_classifications: list[SessionClassification],
    ) -> None:
        write_analysis_rows(
            self.database_path,
            analysis_run,
            message_features,
            session_features,
            session_classifications,
        )

    def table_count(self, table_name: str) -> int:
        return table_count(self.database_path, table_name)

    def list_session_summaries(self) -> tuple[SessionSummary, ...]:
        return list_session_summaries(self.database_path)

    def load_session_bundle(self, session_id: str) -> ParsedSessionBundle | None:
        return load_session_bundle(self.database_path, session_id)

    def info(self) -> StoreInfo:
        return store_info(self.database_path)
