from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from .models import SessionScopeFilters


class TrendBucketSize(StrEnum):
    WEEK = "week"
    MONTH = "month"


class TrendStatus(StrEnum):
    IMPROVING = "improving"
    WORSENING = "worsening"
    DECREASING = "decreasing"
    INCREASING = "increasing"
    NO_MATERIAL_CHANGE = "no_material_change"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class TrendFilters(SessionScopeFilters):
    bucket: TrendBucketSize = TrendBucketSize.WEEK
    periods: int = 12
    limit: int = 10


@dataclass(frozen=True)
class ProjectFilters(SessionScopeFilters):
    limit: int = 10


@dataclass(frozen=True)
class AnalyzerVersionCount:
    analyzer_version: str
    session_count: int


@dataclass(frozen=True)
class AnalysisCompatibilityCounts:
    current: int
    stale: int
    never: int
    version_counts: tuple[AnalyzerVersionCount, ...] = ()


@dataclass(frozen=True)
class TrendWindow:
    start: datetime | None
    end: datetime | None
    latest_session_at: datetime | None

    @property
    def anchor(self) -> str:
        return "latest_matching_session" if self.latest_session_at is not None else "none"


@dataclass(frozen=True)
class TrendScope:
    matching_sessions: int
    windowed_sessions: int
    outside_window_sessions: int
    untimed_sessions: int
    matching_analysis: AnalysisCompatibilityCounts
    windowed_analysis: AnalysisCompatibilityCounts


@dataclass(frozen=True)
class ScoreAggregate:
    metric_name: str
    total: float
    sample_count: int

    @property
    def average(self) -> float | None:
        return self.total / self.sample_count if self.sample_count else None


@dataclass(frozen=True)
class ClassificationAggregate:
    label: str
    session_count: int
    rate: float | None


@dataclass(frozen=True)
class TrendMetrics:
    sessions: int
    current_analyzed: int
    stale_analysis: int
    never_analyzed: int
    scores: tuple[ScoreAggregate, ...]
    classifications: tuple[ClassificationAggregate, ...]
    risky_sessions: int

    @property
    def current_analysis_coverage(self) -> float | None:
        return self.current_analyzed / self.sessions if self.sessions else None

    @property
    def risky_session_rate(self) -> float | None:
        return self.risky_sessions / self.current_analyzed if self.current_analyzed else None


@dataclass(frozen=True)
class TrendBucket:
    start: datetime
    end: datetime
    metrics: TrendMetrics


@dataclass(frozen=True)
class DailyCalendarCell:
    observed_date: date
    start: datetime
    end: datetime
    sessions: int
    current_analyzed: int
    stale_analysis: int
    never_analyzed: int
    risky_sessions: int

    @property
    def current_analysis_coverage(self) -> float | None:
        return self.current_analyzed / self.sessions if self.sessions else None

    @property
    def risky_session_rate(self) -> float | None:
        return self.risky_sessions / self.current_analyzed if self.current_analyzed else None


@dataclass(frozen=True)
class TrendJudgment:
    metric_name: str
    status: TrendStatus
    earlier_value: float | None
    recent_value: float | None
    delta: float | None
    earlier_sample_count: int
    recent_sample_count: int
    earlier_nonempty_buckets: int
    recent_nonempty_buckets: int
    earlier_current_analysis_coverage: float | None
    recent_current_analysis_coverage: float | None
    earlier_sample_coverage: float | None
    recent_sample_coverage: float | None
    threshold: float
    comparison_method: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TrendCohort:
    totals: TrendMetrics
    buckets: tuple[TrendBucket, ...]
    calendar: tuple[DailyCalendarCell, ...]
    judgments: tuple[TrendJudgment, ...]
    agents: tuple[AgentObservation, ...]


@dataclass(frozen=True)
class AgentObservation:
    agent_name: str
    metrics: TrendMetrics


@dataclass(frozen=True)
class ProjectObservation:
    project_path: str
    sessions: int
    top_level_sessions: int
    sidechain_sessions: int
    analysis: AnalysisCompatibilityCounts
    first_session_at: datetime | None
    latest_session_at: datetime | None
    agents: tuple[str, ...]


@dataclass(frozen=True)
class ProjectObservations:
    rows: tuple[ProjectObservation, ...]
    unknown_sessions: int


@dataclass(frozen=True)
class ProjectReport:
    filters: ProjectFilters
    observations: ProjectObservations


@dataclass(frozen=True)
class FamilyExclusionCounts:
    orphan_parent: int
    cycle: int
    cross_agent_parent: int


@dataclass(frozen=True)
class RecurrenceEvidence:
    event_count: int
    session_count: int
    root_family_count: int
    top_level_session_count: int
    sidechain_session_count: int
    agents: tuple[str, ...]
    active_bucket_count: int
    first_at: datetime | None
    most_recent_at: datetime | None
    example_session_id: str


@dataclass(frozen=True)
class FailedCommandPattern:
    command: str
    evidence: RecurrenceEvidence


@dataclass(frozen=True)
class FailedToolResultPattern:
    tool_name: str
    fingerprint_id: str
    evidence: RecurrenceEvidence


@dataclass(frozen=True)
class ProblematicFilePattern:
    path: str
    evidence: RecurrenceEvidence


@dataclass(frozen=True)
class RecurringPatterns:
    family_exclusions: FamilyExclusionCounts
    failed_commands: tuple[FailedCommandPattern, ...]
    failed_tool_results: tuple[FailedToolResultPattern, ...]
    problematic_files: tuple[ProblematicFilePattern, ...]


@dataclass(frozen=True)
class TrendCohorts:
    top_level: TrendCohort
    sidechain: TrendCohort


@dataclass(frozen=True)
class TrendReport:
    filters: TrendFilters
    window: TrendWindow
    scope: TrendScope
    cohorts: TrendCohorts
    projects: ProjectObservations
    recurring_patterns: RecurringPatterns
