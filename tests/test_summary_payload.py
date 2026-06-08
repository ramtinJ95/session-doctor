from __future__ import annotations

from session_doctor.store.models import (
    AgentSessionCount,
    AggregateSummary,
    ClassificationCount,
    FailedCommandSummary,
    ProjectSessionCount,
    RecentRiskSession,
    RepeatedFileSummary,
    SummaryFilters,
)
from session_doctor.summary_payload import summary_payload


def test_summary_payload_uses_stable_machine_readable_keys() -> None:
    summary = AggregateSummary(
        filters=SummaryFilters(agent_name="codex", project_path="/tmp/project", limit=5),
        total_sessions=2,
        analyzed_sessions=1,
        unanalyzed_sessions=1,
        agent_counts=(AgentSessionCount("codex", 2, 1),),
        project_counts=(ProjectSessionCount("/tmp/project", 2, 1),),
        classification_counts=(ClassificationCount("user_stuck", 1),),
        recent_risk_sessions=(
            RecentRiskSession(
                session_id="session-1",
                agent_name="codex",
                started_at="2026-05-06 08:00:00",
                project_path="/tmp/project",
                labels=("user_stuck",),
                friction_score=0.5,
                stuckness_score=0.7,
                agent_fit_risk=0.4,
                max_risk_score=0.7,
            ),
        ),
        failed_commands=(
            FailedCommandSummary("pytest -q", 2, 1, ("codex",), "2026-05-06", "session-1"),
        ),
        repeated_files=(
            RepeatedFileSummary("src/app.py", 3, 1, ("codex",), "2026-05-06", "session-1"),
        ),
        recommendations=("Inspect session-1 next.",),
    )

    payload = summary_payload(summary)

    assert set(payload) == {
        "filters",
        "totals",
        "agents",
        "projects",
        "classifications",
        "recent_risk_sessions",
        "failed_commands",
        "repeated_files",
        "recommendations",
    }
    assert payload["filters"] == {"agent": "codex", "project": "/tmp/project", "limit": 5}
    assert payload["totals"] == {
        "sessions": 2,
        "analyzed_sessions": 1,
        "unanalyzed_sessions": 1,
    }
    assert payload["recent_risk_sessions"] == [
        {
            "session_id": "session-1",
            "agent": "codex",
            "started_at": "2026-05-06 08:00:00",
            "project": "/tmp/project",
            "labels": ["user_stuck"],
            "friction_score": 0.5,
            "stuckness_score": 0.7,
            "agent_fit_risk": 0.4,
            "max_risk_score": 0.7,
        }
    ]
