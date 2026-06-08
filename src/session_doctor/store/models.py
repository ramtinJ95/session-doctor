from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoreInfo:
    database_path: Path
    exists: bool
    schema_version: int | None
    tables: tuple[str, ...]


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    agent_name: str
    started_at: str | None
    ended_at: str | None
    cwd: str | None
    project_path: str | None
    source_path: str | None
    message_count: int
    response_item_message_count: int
    event_msg_fallback_count: int
    command_count: int
    warning_count: int


@dataclass(frozen=True)
class SummaryFilters:
    agent_name: str | None = None
    project_path: str | None = None
    limit: int = 10


@dataclass(frozen=True)
class AgentSessionCount:
    agent_name: str
    session_count: int
    analyzed_session_count: int


@dataclass(frozen=True)
class ProjectSessionCount:
    project_path: str
    session_count: int
    analyzed_session_count: int


@dataclass(frozen=True)
class ClassificationCount:
    label: str
    session_count: int


@dataclass(frozen=True)
class RecentRiskSession:
    session_id: str
    agent_name: str
    started_at: str | None
    project_path: str | None
    labels: tuple[str, ...]
    friction_score: float | None
    stuckness_score: float | None
    agent_fit_risk: float | None
    max_risk_score: float


@dataclass(frozen=True)
class FailedCommandSummary:
    command: str
    failure_count: int
    session_count: int
    agents: tuple[str, ...]
    most_recent_at: str | None
    example_session_id: str


@dataclass(frozen=True)
class RepeatedFileSummary:
    path: str
    activity_count: int
    session_count: int
    agents: tuple[str, ...]
    most_recent_at: str | None
    example_session_id: str


@dataclass(frozen=True)
class AggregateSummary:
    filters: SummaryFilters
    total_sessions: int
    analyzed_sessions: int
    unanalyzed_sessions: int
    agent_counts: tuple[AgentSessionCount, ...]
    project_counts: tuple[ProjectSessionCount, ...]
    classification_counts: tuple[ClassificationCount, ...]
    recent_risk_sessions: tuple[RecentRiskSession, ...]
    failed_commands: tuple[FailedCommandSummary, ...]
    repeated_files: tuple[RepeatedFileSummary, ...]
    recommendations: tuple[str, ...]
