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

from .analysis_readers import AnalysisTarget, list_analysis_targets
from .connection import initialize_database, inspection_connection
from .json_values import duckdb_value, metadata_json, parse_metadata, parse_string_list
from .migrations import require_current_schema
from .models import AggregateSummary, SessionScopeFilters, SessionSummary, StoreInfo, SummaryFilters
from .project_readers import read_projects
from .readers import (
    list_session_summaries,
    load_session_bundle,
    store_info,
    table_count,
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
from .summary_readers import aggregate_summary as read_aggregate_summary
from .trend_models import ProjectFilters, ProjectReport, TrendFilters, TrendReport
from .trend_readers import read_trends
from .writers import (
    insert_parsed_bundle as write_parsed_bundle,
)
from .writers import (
    replace_analysis_rows as write_analysis_rows,
)

__all__ = [
    "DuckDBStore",
    "AggregateSummary",
    "SessionSummary",
    "StoreInfo",
    "SummaryFilters",
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
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser()

    def initialize(self) -> StoreInfo:
        initialize_database(self.database_path)
        return self.info()

    def validate_schema(self, *, allow_empty: bool = False) -> None:
        with inspection_connection(self.database_path) as connection:
            require_current_schema(connection, allow_empty=allow_empty)

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

    def aggregate_summary(self, filters: SummaryFilters) -> AggregateSummary:
        return read_aggregate_summary(self.database_path, filters)

    def list_analysis_targets(
        self,
        filters: SessionScopeFilters,
    ) -> tuple[AnalysisTarget, ...]:
        return list_analysis_targets(self.database_path, filters)

    def trends(self, filters: TrendFilters) -> TrendReport:
        return read_trends(self.database_path, filters)

    def projects(self, filters: ProjectFilters) -> ProjectReport:
        return read_projects(self.database_path, filters)

    def load_session_bundle(self, session_id: str) -> ParsedSessionBundle | None:
        return load_session_bundle(self.database_path, session_id)

    def info(self) -> StoreInfo:
        return store_info(self.database_path)
