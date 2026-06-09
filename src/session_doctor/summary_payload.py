from __future__ import annotations

from .privacy import redact_home
from .store.models import AggregateSummary


def summary_payload(summary: AggregateSummary) -> dict[str, object]:
    return {
        "filters": {
            "agent": summary.filters.agent_name,
            "project": (
                redact_home(summary.filters.project_path) if summary.filters.project_path else None
            ),
            "limit": summary.filters.limit,
        },
        "totals": {
            "sessions": summary.total_sessions,
            "analyzed_sessions": summary.analyzed_sessions,
            "unanalyzed_sessions": summary.unanalyzed_sessions,
        },
        "agents": [
            {
                "agent": row.agent_name,
                "sessions": row.session_count,
                "analyzed_sessions": row.analyzed_session_count,
            }
            for row in summary.agent_counts
        ],
        "projects": [
            {
                "project": row.project_path,
                "sessions": row.session_count,
                "analyzed_sessions": row.analyzed_session_count,
            }
            for row in summary.project_counts
        ],
        "classifications": [
            {"label": row.label, "sessions": row.session_count}
            for row in summary.classification_counts
        ],
        "recent_risk_sessions": [
            {
                "session_id": row.session_id,
                "agent": row.agent_name,
                "started_at": row.started_at,
                "project": row.project_path,
                "labels": list(row.labels),
                "friction_score": row.friction_score,
                "stuckness_score": row.stuckness_score,
                "agent_fit_risk": row.agent_fit_risk,
                "max_risk_score": row.max_risk_score,
            }
            for row in summary.recent_risk_sessions
        ],
        "failed_commands": [
            {
                "command": row.command,
                "failures": row.failure_count,
                "sessions": row.session_count,
                "agents": list(row.agents),
                "most_recent_at": row.most_recent_at,
                "example_session_id": row.example_session_id,
            }
            for row in summary.failed_commands
        ],
        "repeated_files": [
            {
                "path": row.path,
                "activities": row.activity_count,
                "sessions": row.session_count,
                "agents": list(row.agents),
                "most_recent_at": row.most_recent_at,
                "example_session_id": row.example_session_id,
            }
            for row in summary.repeated_files
        ],
        "recommendations": list(summary.recommendations),
    }
