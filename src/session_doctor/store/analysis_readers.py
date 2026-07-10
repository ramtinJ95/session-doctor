from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from session_doctor.analysis.version import ANALYZER_VERSION
from session_doctor.diagnostic_models import AnalysisCompatibility

from .aggregate_queries import base_sessions_cte, latest_analysis_sql
from .connection import read_connection
from .models import SessionScopeFilters


@dataclass(frozen=True)
class AnalysisTarget:
    session_id: str
    started_at: datetime | None
    compatibility: AnalysisCompatibility
    analyzer_version: str | None


def list_analysis_targets(
    database_path: Path,
    filters: SessionScopeFilters,
) -> tuple[AnalysisTarget, ...]:
    base_cte, params = base_sessions_cte(filters)
    with read_connection(database_path) as connection:
        rows = connection.execute(
            f"""
            WITH {base_cte},
            latest_analysis AS ({latest_analysis_sql()})
            SELECT
                b.session_id,
                b.started_at,
                la.analyzer_version
            FROM base_sessions AS b
            LEFT JOIN latest_analysis AS la ON la.session_id = b.session_id
            ORDER BY b.started_at ASC NULLS LAST, b.session_id ASC
            """,
            params,
        ).fetchall()

    return tuple(
        AnalysisTarget(
            session_id=str(session_id),
            started_at=started_at,
            compatibility=analysis_compatibility(analyzer_version),
            analyzer_version=str(analyzer_version) if analyzer_version is not None else None,
        )
        for session_id, started_at, analyzer_version in rows
    )


def analysis_compatibility(analyzer_version: object | None) -> AnalysisCompatibility:
    if analyzer_version is None:
        return AnalysisCompatibility.MISSING
    if str(analyzer_version) == ANALYZER_VERSION:
        return AnalysisCompatibility.CURRENT
    return AnalysisCompatibility.STALE
