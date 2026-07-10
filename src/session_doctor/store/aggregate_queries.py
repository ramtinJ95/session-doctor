from __future__ import annotations

from .models import SessionScopeFilters

SCORE_NAMES = (
    "friction_score",
    "stuckness_score",
    "prompt_clarity_risk",
    "agent_fit_risk",
    "project_complexity_signal",
)
RISK_LABELS = (
    "user_stuck",
    "tooling_blocked",
    "agent_looping",
    "agent_misunderstood",
    "prompt_ambiguous",
    "task_too_large",
    "repo_complexity_high",
    "abandoned_or_stopped",
)
PRIMARY_RISK_LABELS = ("user_stuck", "tooling_blocked", "agent_looping")
RISK_SCORE_THRESHOLD = 0.55


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
        project_path = filters.project_path.rstrip("/") or "/"
        project_prefix = "/" if project_path == "/" else f"{project_path}/"
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


def score_features_sql() -> str:
    return """
    SELECT
        sf.session_id,
        MAX(CASE WHEN sf.feature_name = 'friction_score' THEN sf.score END) AS friction_score,
        MAX(CASE WHEN sf.feature_name = 'stuckness_score' THEN sf.score END) AS stuckness_score,
        MAX(CASE WHEN sf.feature_name = 'prompt_clarity_risk' THEN sf.score END)
            AS prompt_clarity_risk,
        MAX(CASE WHEN sf.feature_name = 'agent_fit_risk' THEN sf.score END) AS agent_fit_risk,
        MAX(CASE WHEN sf.feature_name = 'project_complexity_signal' THEN sf.score END)
            AS project_complexity_signal
    FROM session_features AS sf
    JOIN eligible_analysis AS ea ON ea.analysis_run_id = sf.analysis_run_id
    GROUP BY sf.session_id
    """


def label_groups_sql() -> str:
    risk_labels = sql_string_list(RISK_LABELS)
    primary_risk_labels = sql_string_list(PRIMARY_RISK_LABELS)
    return f"""
    SELECT
        sc.session_id,
        string_agg(DISTINCT sc.label, ',' ORDER BY sc.label) AS labels,
        COUNT(DISTINCT CASE WHEN sc.label IN ({risk_labels}) THEN sc.label END)
            AS risk_label_count,
        COUNT(DISTINCT CASE WHEN sc.label IN ({primary_risk_labels}) THEN sc.label END)
            AS primary_risk_label_count
    FROM session_classifications AS sc
    JOIN eligible_analysis AS ea ON ea.analysis_run_id = sc.analysis_run_id
    GROUP BY sc.session_id
    """


def sql_string_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
