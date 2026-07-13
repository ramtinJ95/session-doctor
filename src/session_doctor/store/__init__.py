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
from .lifecycle import LifecycleObservation
from .migrations import DURABLE_TABLE_NAMES, SCHEMA_VERSION, TABLE_NAMES, SchemaMismatchError
from .normalization_runs import (
    NORMALIZATION_CONFIGURATION_HASH,
    NORMALIZATION_VERSION,
    NormalizationConflictError,
    NormalizationCoverage,
    NormalizationRun,
    StoredNormalization,
)
from .semantic_runs import (
    SemanticAnalysisConflictError,
    SemanticAnalysisRun,
)
from .snapshot_history import (
    PruneDependencies,
    PruneResult,
    SnapshotPruneBlocked,
    SnapshotSummary,
)
from .snapshots import (
    BundleMemberCapture,
    CapturedBundle,
    CapturedSource,
    LoadedBundleMember,
    SnapshotSourceMismatchError,
)
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
    "BundleMemberCapture",
    "CaptureProvenanceError",
    "DatabaseOpenError",
    "DuckDBStore",
    "DURABLE_TABLE_NAMES",
    "SCHEMA_VERSION",
    "SchemaMismatchError",
    "ProjectFilters",
    "ProjectReport",
    "PruneResult",
    "PruneDependencies",
    "SessionSummary",
    "SessionScopeFilters",
    "StoreInfo",
    "StaleCaptureError",
    "SnapshotSourceMismatchError",
    "SnapshotPruneBlocked",
    "SnapshotSummary",
    "SummaryFilters",
    "TABLE_NAMES",
    "TrendBucketSize",
    "TrendFilters",
    "TrendReport",
    "TrendStatus",
    "load_diagnostic_snapshot",
    "LifecycleObservation",
    "NORMALIZATION_CONFIGURATION_HASH",
    "NORMALIZATION_VERSION",
    "NormalizationConflictError",
    "NormalizationCoverage",
    "NormalizationRun",
    "StoredNormalization",
    "SemanticAnalysisConflictError",
    "SemanticAnalysisRun",
    "LoadedBundleMember",
]
