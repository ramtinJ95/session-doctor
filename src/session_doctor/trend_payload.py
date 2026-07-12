from __future__ import annotations

from datetime import datetime

from .analysis import ANALYZER_VERSION
from .privacy import redact_home
from .store.trend_models import (
    AgentObservation,
    AnalysisCompatibilityCounts,
    DailyCalendarCell,
    ProjectObservation,
    ProjectReport,
    RecurrenceEvidence,
    TrendBucket,
    TrendCohort,
    TrendJudgment,
    TrendMetrics,
    TrendReport,
)
from .summary_payload import rounded_score


def trend_payload(report: TrendReport) -> dict[str, object]:
    return {
        "filters": {
            "project": (
                redact_home(report.filters.project_path) if report.filters.project_path else None
            ),
            "agent": report.filters.agent_name,
            "bucket": report.filters.bucket.value,
            "periods": report.filters.periods,
            "limit": report.filters.limit,
        },
        "window": {
            "start": timestamp_value(report.window.start),
            "end": timestamp_value(report.window.end),
            "anchor": report.window.anchor,
            "latest_session_at": timestamp_value(report.window.latest_session_at),
        },
        "scope": {
            "matching_sessions": report.scope.matching_sessions,
            "windowed_sessions": report.scope.windowed_sessions,
            "outside_window_sessions": report.scope.outside_window_sessions,
            "untimed_sessions": report.scope.untimed_sessions,
            "analysis_compatibility": {
                "current_analyzer_version": ANALYZER_VERSION,
                "matching": compatibility_payload(report.scope.matching_analysis),
                "windowed": compatibility_payload(report.scope.windowed_analysis),
            },
        },
        "cohorts": {
            "top_level": cohort_payload(report.cohorts.top_level),
            "sidechain": cohort_payload(report.cohorts.sidechain),
        },
        "projects": [project_observation_payload(row) for row in report.projects.rows],
        "unknown_project_sessions": report.projects.unknown_sessions,
        "recurring_patterns": recurring_patterns_payload(report),
    }


def compatibility_payload(counts: AnalysisCompatibilityCounts) -> dict[str, object]:
    return {
        "current": counts.current,
        "stale": counts.stale,
        "never": counts.never,
        "version_counts": {
            row.analyzer_version: row.session_count for row in counts.version_counts
        },
    }


def cohort_payload(cohort: TrendCohort) -> dict[str, object]:
    return {
        "totals": metrics_payload(cohort.totals),
        "buckets": [bucket_payload(bucket) for bucket in cohort.buckets],
        "calendar": [calendar_cell_payload(cell) for cell in cohort.calendar],
        "judgments": [judgment_payload(judgment) for judgment in cohort.judgments],
        "agents": [agent_observation_payload(agent) for agent in cohort.agents],
    }


def bucket_payload(bucket: TrendBucket) -> dict[str, object]:
    return {
        "start": timestamp_value(bucket.start),
        "end": timestamp_value(bucket.end),
        **metrics_payload(bucket.metrics),
    }


def calendar_cell_payload(cell: DailyCalendarCell) -> dict[str, object]:
    return {
        "observed_date": cell.observed_date.isoformat(),
        "start": timestamp_value(cell.start),
        "end": timestamp_value(cell.end),
        "sessions": cell.sessions,
        "analysis": {
            "current": cell.current_analyzed,
            "stale": cell.stale_analysis,
            "never": cell.never_analyzed,
            "coverage": rounded_score(cell.current_analysis_coverage),
        },
        "risk": {
            "risky_sessions": cell.risky_sessions,
            "current_analyzed_sessions": cell.current_analyzed,
            "rate": rounded_score(cell.risky_session_rate),
        },
    }


def metrics_payload(metrics: TrendMetrics) -> dict[str, object]:
    return {
        "sessions": metrics.sessions,
        "analysis": {
            "current": metrics.current_analyzed,
            "stale": metrics.stale_analysis,
            "never": metrics.never_analyzed,
            "coverage": rounded_score(metrics.current_analysis_coverage),
        },
        "scores": {
            score.metric_name: {
                "average": rounded_score(score.average),
                "samples": score.sample_count,
            }
            for score in metrics.scores
        },
        "classifications": [
            {
                "label": classification.label,
                "sessions": classification.session_count,
                "rate": rounded_score(classification.rate),
            }
            for classification in metrics.classifications
        ],
        "risk": {
            "risky_sessions": metrics.risky_sessions,
            "rate": rounded_score(metrics.risky_session_rate),
        },
    }


def judgment_payload(judgment: TrendJudgment) -> dict[str, object]:
    return {
        "metric": judgment.metric_name,
        "status": judgment.status.value,
        "earlier_value": rounded_score(judgment.earlier_value),
        "recent_value": rounded_score(judgment.recent_value),
        "delta": rounded_score(judgment.delta),
        "earlier_samples": judgment.earlier_sample_count,
        "recent_samples": judgment.recent_sample_count,
        "earlier_nonempty_buckets": judgment.earlier_nonempty_buckets,
        "recent_nonempty_buckets": judgment.recent_nonempty_buckets,
        "earlier_current_analysis_coverage": rounded_score(
            judgment.earlier_current_analysis_coverage
        ),
        "recent_current_analysis_coverage": rounded_score(
            judgment.recent_current_analysis_coverage
        ),
        "earlier_sample_coverage": rounded_score(judgment.earlier_sample_coverage),
        "recent_sample_coverage": rounded_score(judgment.recent_sample_coverage),
        "threshold": judgment.threshold,
        "comparison_method": judgment.comparison_method,
        "reasons": list(judgment.reasons),
    }


def timestamp_value(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def agent_observation_payload(observation: AgentObservation) -> dict[str, object]:
    return {"agent": observation.agent_name, **metrics_payload(observation.metrics)}


def project_observation_payload(observation: ProjectObservation) -> dict[str, object]:
    return {
        "project": redact_home(observation.project_path),
        "sessions": observation.sessions,
        "top_level_sessions": observation.top_level_sessions,
        "sidechain_sessions": observation.sidechain_sessions,
        "analysis": compatibility_payload(observation.analysis),
        "first_session_at": timestamp_value(observation.first_session_at),
        "latest_session_at": timestamp_value(observation.latest_session_at),
        "agents": list(observation.agents),
    }


def project_payload(report: ProjectReport) -> dict[str, object]:
    return {
        "filters": {"agent": report.filters.agent_name, "limit": report.filters.limit},
        "projects": [project_observation_payload(row) for row in report.observations.rows],
        "unknown_project_sessions": report.observations.unknown_sessions,
    }


def recurring_patterns_payload(report: TrendReport) -> dict[str, object]:
    patterns = report.recurring_patterns
    return {
        "family_exclusions": {
            "scope": "matching_sessions",
            "orphan_parent": patterns.family_exclusions.orphan_parent,
            "cycle": patterns.family_exclusions.cycle,
            "cross_agent_parent": patterns.family_exclusions.cross_agent_parent,
        },
        "failed_commands": [
            {"command": row.command, **recurrence_evidence_payload(row.evidence)}
            for row in patterns.failed_commands
        ],
        "failed_tool_results": [
            {
                "tool_name": row.tool_name,
                "fingerprint_id": row.fingerprint_id,
                **recurrence_evidence_payload(row.evidence),
            }
            for row in patterns.failed_tool_results
        ],
        "problematic_files": [
            {"path": redact_home(row.path), **recurrence_evidence_payload(row.evidence)}
            for row in patterns.problematic_files
        ],
    }


def recurrence_evidence_payload(evidence: RecurrenceEvidence) -> dict[str, object]:
    return {
        "events": evidence.event_count,
        "sessions": evidence.session_count,
        "root_families": evidence.root_family_count,
        "top_level_sessions": evidence.top_level_session_count,
        "sidechain_sessions": evidence.sidechain_session_count,
        "agents": list(evidence.agents),
        "active_buckets": evidence.active_bucket_count,
        "first_at": timestamp_value(evidence.first_at),
        "most_recent_at": timestamp_value(evidence.most_recent_at),
        "example_session_id": evidence.example_session_id,
    }
