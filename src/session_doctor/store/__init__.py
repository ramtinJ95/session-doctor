from __future__ import annotations

from .analysis_readers import AnalysisCompatibility, AnalysisTarget
from .connection import DatabaseOpenError
from .duckdb import (
    AggregateSummary,
    DuckDBStore,
    SessionScopeFilters,
    SessionSummary,
    StoreInfo,
    SummaryFilters,
)
from .migrations import SCHEMA_VERSION, TABLE_NAMES, SchemaMismatchError
from .trend_models import (
    ProjectFilters,
    ProjectReport,
    TrendBucketSize,
    TrendFilters,
    TrendReport,
    TrendStatus,
)

__all__ = [
    "AggregateSummary",
    "AnalysisCompatibility",
    "AnalysisTarget",
    "DatabaseOpenError",
    "DuckDBStore",
    "SCHEMA_VERSION",
    "SchemaMismatchError",
    "ProjectFilters",
    "ProjectReport",
    "SessionSummary",
    "SessionScopeFilters",
    "StoreInfo",
    "SummaryFilters",
    "TABLE_NAMES",
    "TrendBucketSize",
    "TrendFilters",
    "TrendReport",
    "TrendStatus",
]
