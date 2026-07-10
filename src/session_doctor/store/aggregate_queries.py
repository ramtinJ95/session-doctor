from __future__ import annotations

from .models import SessionScopeFilters


def base_sessions_cte(filters: SessionScopeFilters) -> tuple[str, list[object]]:
    where_sql, params = session_filters(filters, "s")
    return f"base_sessions AS (SELECT s.* FROM sessions AS s {where_sql})", params


def session_filters(filters: SessionScopeFilters, alias: str) -> tuple[str, list[object]]:
    conditions: list[str] = []
    params: list[object] = []
    if filters.agent_name:
        conditions.append(f"{alias}.agent_name = ?")
        params.append(filters.agent_name)
    if filters.project_path:
        project_path = filters.project_path.rstrip("/")
        project_prefix = f"{project_path}/"
        conditions.append(
            "("
            f"{alias}.project_path = ? OR starts_with({alias}.project_path, ?) "
            f"OR {alias}.cwd = ? OR starts_with({alias}.cwd, ?)"
            ")"
        )
        params.extend([project_path, project_prefix, project_path, project_prefix])
    if not conditions:
        return "", params
    return "WHERE " + " AND ".join(conditions), params


def latest_analysis_sql() -> str:
    return """
    SELECT analysis_run_id, session_id, analyzer_version
    FROM (
        SELECT
            ar.analysis_run_id,
            ar.session_id,
            ar.analyzer_version,
            ROW_NUMBER() OVER (
                PARTITION BY ar.session_id
                ORDER BY ar.completed_at DESC NULLS LAST,
                    ar.started_at DESC NULLS LAST,
                    ar.analysis_run_id DESC
            ) AS row_number
        FROM analysis_runs AS ar
    )
    WHERE row_number = 1
    """
