from __future__ import annotations

from pathlib import Path

from .connection import read_connection
from .trend_models import ProjectFilters, ProjectReport, TrendFilters
from .trend_readers import project_observations, session_trend_rows


def read_projects(database_path: Path, filters: ProjectFilters) -> ProjectReport:
    trend_filters = TrendFilters(
        agent_name=filters.agent_name,
        project_path=None,
        limit=filters.limit,
    )
    with read_connection(database_path) as connection:
        rows = session_trend_rows(connection, trend_filters)
    return ProjectReport(
        filters=filters,
        observations=project_observations(rows, filters.limit),
    )
