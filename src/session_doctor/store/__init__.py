from __future__ import annotations

from .analysis_readers import AnalysisCompatibility, AnalysisTarget
from .connection import DatabaseOpenError
from .diagnostic_readers import load_diagnostic_snapshot
from .duckdb import (
    AggregateSummary,
    DuckDBStore,
    SessionScopeFilters,
    SessionSummary,
    StoreInfo,
    SummaryFilters,
)
from .migrations import DURABLE_TABLE_NAMES, SCHEMA_VERSION, TABLE_NAMES, SchemaMismatchError
from .snapshots import CapturedBundle, CapturedSource
from .trend_models import (
    ProjectFilters,
    ProjectReport,
    TrendBucketSize,
    TrendFilters,
    TrendReport,
    TrendStatus,
)
from .writers import CaptureProvenanceError, StaleCaptureError

__all__ = [
    "AggregateSummary",
    "AnalysisCompatibility",
    "AnalysisTarget",
    "CapturedSource",
    "CapturedBundle",
    "CaptureProvenanceError",
    "DatabaseOpenError",
    "DuckDBStore",
    "DURABLE_TABLE_NAMES",
    "SCHEMA_VERSION",
    "SchemaMismatchError",
    "ProjectFilters",
    "ProjectReport",
    "SessionSummary",
    "SessionScopeFilters",
    "StoreInfo",
    "StaleCaptureError",
    "SummaryFilters",
    "TABLE_NAMES",
    "TrendBucketSize",
    "TrendFilters",
    "TrendReport",
    "TrendStatus",
    "load_diagnostic_snapshot",
]
