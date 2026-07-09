from __future__ import annotations

import posixpath
from dataclasses import dataclass, field, replace
from pathlib import Path

import duckdb

from session_doctor.privacy import redact_home

from .connection import read_connection
from .models import (
    AgentSessionCount,
    AggregateSummary,
    ClassificationCount,
    FailedCommandSummary,
    ProjectSessionCount,
    RecentRiskSession,
    RepeatedFileSummary,
    SummaryFilters,
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
PROBLEMATIC_SESSION_SCORE_THRESHOLD = 0.55
MUTATING_FILE_OPERATIONS = ("edit", "update", "write", "patch", "move", "delete")


@dataclass
class CommandGroup:
    display_command: str = ""
    failure_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    agents: set[str] = field(default_factory=set)
    most_recent_at: str | None = None
    example_session_id: str = ""


@dataclass
class FileGroup:
    display_path: str = ""
    activity_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    agents: set[str] = field(default_factory=set)
    most_recent_at: str | None = None
    example_session_id: str = ""


def aggregate_summary(database_path: Path, filters: SummaryFilters) -> AggregateSummary:
    normalized_filters = replace(filters, limit=max(filters.limit, 1))
    if not database_path.exists():
        return empty_summary(normalized_filters)

    with read_connection(database_path) as connection:
        total_sessions = count_sessions(connection, normalized_filters)
        analyzed_sessions = count_analyzed_sessions(connection, normalized_filters)
        uncapped_classification_counts = classification_counts(
            connection,
            normalized_filters,
            limit=None,
        )
        summary = AggregateSummary(
            filters=normalized_filters,
            total_sessions=total_sessions,
            analyzed_sessions=analyzed_sessions,
            unanalyzed_sessions=max(total_sessions - analyzed_sessions, 0),
            agent_counts=agent_counts(connection, normalized_filters),
            project_counts=project_counts(connection, normalized_filters),
            classification_counts=classification_counts(
                connection,
                normalized_filters,
                limit=normalized_filters.limit,
            ),
            recent_risk_sessions=recent_risk_sessions(connection, normalized_filters),
            failed_commands=failed_commands(connection, normalized_filters),
            repeated_files=repeated_files(connection, normalized_filters),
            recommendations=(),
        )

    return replace(
        summary,
        recommendations=recommendations_for_summary(summary, uncapped_classification_counts),
    )


def empty_summary(filters: SummaryFilters) -> AggregateSummary:
    return AggregateSummary(
        filters=filters,
        total_sessions=0,
        analyzed_sessions=0,
        unanalyzed_sessions=0,
        agent_counts=(),
        project_counts=(),
        classification_counts=(),
        recent_risk_sessions=(),
        failed_commands=(),
        repeated_files=(),
        recommendations=("Ingest Codex or Pi sessions before running summary.",),
    )


def count_sessions(connection: duckdb.DuckDBPyConnection, filters: SummaryFilters) -> int:
    where_sql, params = session_filters(filters, "s")
    row = connection.execute(
        f"SELECT COUNT(DISTINCT s.session_id) FROM sessions AS s {where_sql}",
        params,
    ).fetchone()
    return int(row[0]) if row else 0


def count_analyzed_sessions(connection: duckdb.DuckDBPyConnection, filters: SummaryFilters) -> int:
    base_sessions_sql, params = base_sessions_cte(filters)
    row = connection.execute(
        f"""
        WITH {base_sessions_sql},
        latest_analysis AS ({latest_analysis_sql()})
        SELECT COUNT(DISTINCT b.session_id)
        FROM base_sessions AS b
        JOIN latest_analysis AS la ON la.session_id = b.session_id
        """,
        params,
    ).fetchone()
    return int(row[0]) if row else 0


def agent_counts(
    connection: duckdb.DuckDBPyConnection,
    filters: SummaryFilters,
) -> tuple[AgentSessionCount, ...]:
    base_sessions_sql, params = base_sessions_cte(filters)
    rows = connection.execute(
        f"""
        WITH {base_sessions_sql},
        latest_analysis AS ({latest_analysis_sql()})
        SELECT
            b.agent_name,
            COUNT(DISTINCT b.session_id) AS session_count,
            COUNT(DISTINCT la.session_id) AS analyzed_session_count
        FROM base_sessions AS b
        LEFT JOIN latest_analysis AS la ON la.session_id = b.session_id
        GROUP BY b.agent_name
        ORDER BY session_count DESC, b.agent_name
        """,
        params,
    ).fetchall()
    return tuple(AgentSessionCount(str(row[0]), int(row[1]), int(row[2])) for row in rows)


def project_counts(
    connection: duckdb.DuckDBPyConnection,
    filters: SummaryFilters,
) -> tuple[ProjectSessionCount, ...]:
    base_sessions_sql, params = base_sessions_cte(filters)
    rows = connection.execute(
        f"""
        WITH {base_sessions_sql},
        latest_analysis AS ({latest_analysis_sql()}),
        session_projects AS (
            SELECT
                b.session_id,
                COALESCE(NULLIF(b.project_path, ''), NULLIF(b.cwd, ''), '(unknown)')
                    AS project_path
            FROM base_sessions AS b
        )
        SELECT
            sp.project_path,
            COUNT(DISTINCT sp.session_id) AS session_count,
            COUNT(DISTINCT la.session_id) AS analyzed_session_count
        FROM session_projects AS sp
        LEFT JOIN latest_analysis AS la ON la.session_id = sp.session_id
        GROUP BY sp.project_path
        ORDER BY session_count DESC, sp.project_path
        LIMIT ?
        """,
        [*params, filters.limit],
    ).fetchall()
    return tuple(
        ProjectSessionCount(redact_home(str(row[0])), int(row[1]), int(row[2])) for row in rows
    )


def classification_counts(
    connection: duckdb.DuckDBPyConnection,
    filters: SummaryFilters,
    limit: int | None = None,
) -> tuple[ClassificationCount, ...]:
    base_sessions_sql, params = base_sessions_cte(filters)
    limit_sql = "" if limit is None else "LIMIT ?"
    query_params = params if limit is None else [*params, limit]
    rows = connection.execute(
        f"""
        WITH {base_sessions_sql},
        latest_analysis AS ({latest_analysis_sql()})
        SELECT
            sc.label,
            COUNT(DISTINCT sc.session_id) AS session_count
        FROM session_classifications AS sc
        JOIN latest_analysis AS la ON la.analysis_run_id = sc.analysis_run_id
        JOIN base_sessions AS b ON b.session_id = sc.session_id
        GROUP BY sc.label
        ORDER BY session_count DESC, sc.label
        {limit_sql}
        """,
        query_params,
    ).fetchall()
    return tuple(ClassificationCount(str(row[0]), int(row[1])) for row in rows)


def recent_risk_sessions(
    connection: duckdb.DuckDBPyConnection,
    filters: SummaryFilters,
) -> tuple[RecentRiskSession, ...]:
    base_sessions_sql, params = base_sessions_cte(filters)
    rows = connection.execute(
        f"""
        WITH {base_sessions_sql},
        latest_analysis AS ({latest_analysis_sql()}),
        score_features AS ({score_features_sql()}),
        label_groups AS ({label_groups_sql()})
        SELECT
            b.session_id,
            b.agent_name,
            CAST(b.started_at AS VARCHAR) AS started_at,
            COALESCE(NULLIF(b.project_path, ''), NULLIF(b.cwd, '')) AS project_path,
            COALESCE(lg.labels, '') AS labels,
            sf.friction_score,
            sf.stuckness_score,
            sf.prompt_clarity_risk,
            sf.agent_fit_risk,
            sf.project_complexity_signal,
            GREATEST(
                COALESCE(sf.friction_score, 0),
                COALESCE(sf.stuckness_score, 0),
                COALESCE(sf.agent_fit_risk, 0),
                COALESCE(sf.prompt_clarity_risk, 0),
                COALESCE(sf.project_complexity_signal, 0)
            ) AS max_risk_score,
            COALESCE(lg.primary_risk_label_count, 0) AS primary_risk_label_count,
            COALESCE(lg.risk_label_count, 0) AS risk_label_count
        FROM base_sessions AS b
        JOIN latest_analysis AS la ON la.session_id = b.session_id
        LEFT JOIN score_features AS sf ON sf.session_id = b.session_id
        LEFT JOIN label_groups AS lg ON lg.session_id = b.session_id
        WHERE COALESCE(lg.risk_label_count, 0) > 0
           OR GREATEST(
                COALESCE(sf.friction_score, 0),
                COALESCE(sf.stuckness_score, 0),
                COALESCE(sf.agent_fit_risk, 0),
                COALESCE(sf.prompt_clarity_risk, 0),
                COALESCE(sf.project_complexity_signal, 0)
            ) >= ?
        ORDER BY
            max_risk_score DESC,
            primary_risk_label_count DESC,
            b.started_at DESC NULLS LAST,
            b.session_id
        LIMIT ?
        """,
        [*params, RISK_SCORE_THRESHOLD, filters.limit],
    ).fetchall()
    return tuple(
        RecentRiskSession(
            session_id=str(row[0]),
            agent_name=str(row[1]),
            started_at=row[2],
            project_path=redact_home(row[3]) if row[3] else None,
            labels=split_labels(row[4]),
            friction_score=optional_float(row[5]),
            stuckness_score=optional_float(row[6]),
            prompt_clarity_risk=optional_float(row[7]),
            agent_fit_risk=optional_float(row[8]),
            project_complexity_signal=optional_float(row[9]),
            max_risk_score=float(row[10] or 0.0),
        )
        for row in rows
    )


def failed_commands(
    connection: duckdb.DuckDBPyConnection,
    filters: SummaryFilters,
) -> tuple[FailedCommandSummary, ...]:
    base_sessions_sql, params = base_sessions_cte(filters)
    rows = connection.execute(
        f"""
        WITH {base_sessions_sql}
        SELECT
            c.command_identity_hash,
            c.command_display,
            c.session_id,
            b.agent_name,
            CAST(COALESCE(c.ended_at, c.started_at, b.started_at) AS VARCHAR) AS failed_at
        FROM command_runs AS c
        JOIN base_sessions AS b ON b.session_id = c.session_id
        WHERE (c.exit_code IS NOT NULL AND c.exit_code != 0)
           OR lower(c.metadata_json) LIKE '%"cancelled": true%'
           OR lower(c.metadata_json) LIKE '%"interrupted": true%'
        """,
        params,
    ).fetchall()

    grouped: dict[str, CommandGroup] = {}
    for command_identity, command_display, session_id, agent_name, failed_at in rows:
        group = grouped.setdefault(
            str(command_identity),
            CommandGroup(
                display_command=str(command_display),
                example_session_id=str(session_id),
            ),
        )
        group.failure_count += 1
        group.session_ids.add(str(session_id))
        group.agents.add(str(agent_name))
        if failed_at and (group.most_recent_at is None or str(failed_at) > group.most_recent_at):
            group.most_recent_at = str(failed_at)
            group.example_session_id = str(session_id)

    summaries = [
        FailedCommandSummary(
            command=group.display_command,
            failure_count=group.failure_count,
            session_count=len(group.session_ids),
            agents=tuple(sorted(group.agents)),
            most_recent_at=group.most_recent_at,
            example_session_id=group.example_session_id,
        )
        for group in grouped.values()
    ]
    summaries.sort(
        key=lambda row: (
            -row.failure_count,
            -row.session_count,
            reverse_sort_value(row.most_recent_at),
            row.command,
        )
    )
    return tuple(summaries[: filters.limit])


def repeated_files(
    connection: duckdb.DuckDBPyConnection,
    filters: SummaryFilters,
) -> tuple[RepeatedFileSummary, ...]:
    base_sessions_sql, params = base_sessions_cte(filters)
    operation_placeholders = ", ".join("?" for _ in MUTATING_FILE_OPERATIONS)
    rows = connection.execute(
        f"""
        WITH {base_sessions_sql},
        latest_analysis AS ({latest_analysis_sql()}),
        score_features AS ({score_features_sql()}),
        label_groups AS ({label_groups_sql()}),
        problematic_sessions AS (
            SELECT b.session_id, b.agent_name, b.started_at
            FROM base_sessions AS b
            JOIN latest_analysis AS la ON la.session_id = b.session_id
            LEFT JOIN score_features AS sf ON sf.session_id = b.session_id
            LEFT JOIN label_groups AS lg ON lg.session_id = b.session_id
            WHERE COALESCE(lg.risk_label_count, 0) > 0
               OR GREATEST(
                    COALESCE(sf.friction_score, 0),
                    COALESCE(sf.stuckness_score, 0),
                    COALESCE(sf.agent_fit_risk, 0),
                    COALESCE(sf.prompt_clarity_risk, 0),
                    COALESCE(sf.project_complexity_signal, 0)
                ) >= ?
        )
        SELECT
            f.normalized_path,
            f.canonical_path,
            f.project_relative_path,
            f.session_id,
            ps.agent_name,
            COALESCE(NULLIF(b.project_path, ''), NULLIF(b.cwd, '')) AS project_path,
            CAST(COALESCE(f.timestamp, ps.started_at) AS VARCHAR) AS activity_at
        FROM file_activities AS f
        JOIN problematic_sessions AS ps ON ps.session_id = f.session_id
        JOIN base_sessions AS b ON b.session_id = f.session_id
        WHERE lower(f.operation) IN ({operation_placeholders})
        """,
        [*params, PROBLEMATIC_SESSION_SCORE_THRESHOLD, *MUTATING_FILE_OPERATIONS],
    ).fetchall()

    grouped: dict[tuple[str, ...], FileGroup] = {}
    for (
        normalized_path,
        canonical_path,
        project_relative_path,
        session_id,
        agent_name,
        project_path,
        activity_at,
    ) in rows:
        if project_relative_path and project_path:
            identity = (
                "project",
                posixpath.normpath(str(project_path)),
                str(project_relative_path),
            )
        elif canonical_path:
            identity = ("absolute", str(canonical_path))
        else:
            identity = ("unresolved", str(session_id), str(normalized_path))
        display_path = redact_home(str(canonical_path or normalized_path))
        group = grouped.setdefault(
            identity,
            FileGroup(display_path=display_path, example_session_id=str(session_id)),
        )
        group.activity_count += 1
        group.session_ids.add(str(session_id))
        group.agents.add(str(agent_name))
        if activity_at and (
            group.most_recent_at is None or str(activity_at) > group.most_recent_at
        ):
            group.most_recent_at = str(activity_at)
            group.example_session_id = str(session_id)

    summaries = [
        RepeatedFileSummary(
            path=group.display_path,
            activity_count=group.activity_count,
            session_count=len(group.session_ids),
            agents=tuple(sorted(group.agents)),
            most_recent_at=group.most_recent_at,
            example_session_id=group.example_session_id,
        )
        for group in grouped.values()
        if group.activity_count > 1 or len(group.session_ids) > 1
    ]
    summaries.sort(
        key=lambda row: (
            -row.activity_count,
            -row.session_count,
            reverse_sort_value(row.most_recent_at),
            row.path,
        )
    )
    return tuple(summaries[: filters.limit])


def recommendations_for_summary(
    summary: AggregateSummary,
    classification_counts: tuple[ClassificationCount, ...] | None = None,
) -> tuple[str, ...]:
    if summary.total_sessions == 0:
        if summary.filters.agent_name or summary.filters.project_path:
            return (
                "No sessions match the current filters; adjust filters or ingest more sessions.",
            )
        return ("Ingest Codex or Pi sessions before running summary.",)

    recommendations: list[str] = []
    if summary.unanalyzed_sessions:
        recommendations.append(
            f"Analyze {summary.unanalyzed_sessions} unanalyzed session(s) to improve rankings."
        )

    label_counts = classification_counts or summary.classification_counts
    classification_counts_by_label = {row.label: row.session_count for row in label_counts}
    if classification_counts_by_label.get("tooling_blocked", 0):
        recommendations.append("Inspect the top failed commands for tooling blockers.")
    if classification_counts_by_label.get("agent_looping", 0):
        recommendations.append("Inspect repeated commands and files for agent loop patterns.")
    if summary.failed_commands:
        top_command = summary.failed_commands[0]
        recommendations.append(
            f"Open session {top_command.example_session_id} for the most common failed command."
        )
    if summary.repeated_files:
        top_file = summary.repeated_files[0]
        recommendations.append(
            f"Review {top_file.path} across problematic sessions for repeated edits."
        )
    if not recommendations and summary.recent_risk_sessions:
        recommendations.append(
            f"Inspect high-risk session {summary.recent_risk_sessions[0].session_id} next."
        )
    if not recommendations:
        recommendations.append("No obvious aggregate risk pattern found in the current filters.")
    return tuple(recommendations[:4])


def base_sessions_cte(filters: SummaryFilters) -> tuple[str, list[object]]:
    where_sql, params = session_filters(filters, "s")
    return f"base_sessions AS (SELECT s.* FROM sessions AS s {where_sql})", params


def session_filters(filters: SummaryFilters, alias: str) -> tuple[str, list[object]]:
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
    SELECT analysis_run_id, session_id
    FROM (
        SELECT
            ar.analysis_run_id,
            ar.session_id,
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
    JOIN latest_analysis AS la ON la.analysis_run_id = sf.analysis_run_id
    GROUP BY sf.session_id
    """


def label_groups_sql() -> str:
    risk_labels = sql_string_list(RISK_LABELS)
    primary_risk_labels = sql_string_list(PRIMARY_RISK_LABELS)
    return f"""
    SELECT
        sc.session_id,
        string_agg(DISTINCT sc.label, ',' ORDER BY sc.label) AS labels,
        SUM(CASE WHEN sc.label IN ({risk_labels}) THEN 1 ELSE 0 END) AS risk_label_count,
        SUM(CASE WHEN sc.label IN ({primary_risk_labels}) THEN 1 ELSE 0 END)
            AS primary_risk_label_count
    FROM session_classifications AS sc
    JOIN latest_analysis AS la ON la.analysis_run_id = sc.analysis_run_id
    GROUP BY sc.session_id
    """


def sql_string_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def split_labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        return ()
    return tuple(label for label in value.split(",") if label)


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return float(str(value))


def reverse_sort_value(value: str | None) -> str:
    return "" if value is None else "".join(chr(255 - ord(char)) for char in value)
