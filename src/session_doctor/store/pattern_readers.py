from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import duckdb

from session_doctor.ids import stable_id
from session_doctor.privacy import redact_command_for_display

from .aggregate_queries import MUTATING_FILE_OPERATIONS, failed_command_predicate
from .trend_models import (
    FailedCommandPattern,
    FailedToolResultPattern,
    FamilyExclusionCounts,
    ProblematicFilePattern,
    RecurrenceEvidence,
    RecurringPatterns,
    TrendFilters,
    TrendWindow,
)
from .trend_readers import SessionTrendRow, bucket_start


@dataclass(frozen=True)
class TopologySession:
    session_id: str
    parent_session_id: str | None
    agent_name: str
    is_sidechain: bool
    started_at: datetime | None


@dataclass(frozen=True)
class FamilyResolution:
    root_session_id: str | None
    exclusion_reason: str | None


@dataclass
class PatternGroup:
    event_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    root_session_ids: set[str] = field(default_factory=set)
    top_level_session_ids: set[str] = field(default_factory=set)
    sidechain_session_ids: set[str] = field(default_factory=set)
    agents: set[str] = field(default_factory=set)
    active_buckets: set[datetime] = field(default_factory=set)
    timestamps: list[datetime] = field(default_factory=list)


class PatternRow(Protocol):
    @property
    def evidence(self) -> RecurrenceEvidence: ...


def read_recurring_patterns(
    connection: duckdb.DuckDBPyConnection,
    filters: TrendFilters,
    window: TrendWindow,
    matching_rows: tuple[SessionTrendRow, ...],
) -> RecurringPatterns:
    topology = topology_sessions(connection)
    matching_ids = {row.session_id for row in matching_rows}
    resolutions = {session_id: resolve_family(session_id, topology) for session_id in matching_ids}
    exclusions = exclusion_counts(resolutions)
    eligible_roots = eligible_root_by_member(
        matching_ids,
        resolutions,
        topology,
        window,
    )
    return RecurringPatterns(
        family_exclusions=exclusions,
        failed_commands=failed_command_patterns(
            connection,
            filters,
            topology,
            eligible_roots,
        ),
        failed_tool_results=failed_tool_result_patterns(
            connection,
            filters,
            topology,
            eligible_roots,
        ),
        problematic_files=problematic_file_patterns(
            connection,
            filters,
            topology,
            eligible_roots,
            {row.session_id for row in matching_rows if row.is_risky},
        ),
    )


def topology_sessions(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, TopologySession]:
    rows = connection.execute(
        """
        SELECT session_id, parent_session_id, agent_name, is_sidechain, started_at
        FROM sessions
        ORDER BY session_id
        """
    ).fetchall()
    return {
        str(row[0]): TopologySession(
            session_id=str(row[0]),
            parent_session_id=str(row[1]) if row[1] is not None else None,
            agent_name=str(row[2]),
            is_sidechain=bool(row[3]),
            started_at=row[4] if isinstance(row[4], datetime) else None,
        )
        for row in rows
    }


def resolve_family(
    session_id: str,
    topology: dict[str, TopologySession],
) -> FamilyResolution:
    current = topology.get(session_id)
    if current is None:
        return FamilyResolution(None, "orphan_parent")
    visited: set[str] = set()
    while current.is_sidechain:
        if current.session_id in visited:
            return FamilyResolution(None, "cycle")
        visited.add(current.session_id)
        if current.parent_session_id is None:
            return FamilyResolution(None, "orphan_parent")
        parent = topology.get(current.parent_session_id)
        if parent is None:
            return FamilyResolution(None, "orphan_parent")
        if parent.agent_name != current.agent_name:
            return FamilyResolution(None, "cross_agent_parent")
        current = parent
    return FamilyResolution(current.session_id, None)


def exclusion_counts(
    resolutions: dict[str, FamilyResolution],
) -> FamilyExclusionCounts:
    reasons = [resolution.exclusion_reason for resolution in resolutions.values()]
    return FamilyExclusionCounts(
        orphan_parent=reasons.count("orphan_parent"),
        cycle=reasons.count("cycle"),
        cross_agent_parent=reasons.count("cross_agent_parent"),
    )


def eligible_root_by_member(
    matching_ids: set[str],
    resolutions: dict[str, FamilyResolution],
    topology: dict[str, TopologySession],
    window: TrendWindow,
) -> dict[str, str]:
    eligible: dict[str, str] = {}
    if window.start is None or window.end is None:
        return eligible
    for session_id, resolution in resolutions.items():
        root_id = resolution.root_session_id
        if root_id is None or root_id not in matching_ids:
            continue
        root = topology[root_id]
        if root.started_at is None or not (window.start <= root.started_at < window.end):
            continue
        eligible[session_id] = root_id
    return eligible


def failed_command_patterns(
    connection: duckdb.DuckDBPyConnection,
    filters: TrendFilters,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
) -> tuple[FailedCommandPattern, ...]:
    rows = connection.execute(
        f"""
        SELECT
            c.command_identity_hash,
            c.command_display,
            c.session_id,
            COALESCE(c.ended_at, c.started_at)
        FROM command_runs AS c
        WHERE {failed_command_predicate("c")}
        ORDER BY c.command_identity_hash, c.session_id, c.command_run_id
        """
    ).fetchall()
    groups: dict[str, tuple[str, PatternGroup]] = {}
    for identity, display, session_id, timestamp in rows:
        member_id = str(session_id)
        if member_id not in eligible_roots:
            continue
        display_command = redact_command_for_display(str(display))
        _, group = groups.setdefault(str(identity), (display_command, PatternGroup()))
        add_pattern_event(group, member_id, timestamp, topology, eligible_roots, filters)
    patterns = [
        FailedCommandPattern(command=display, evidence=pattern_evidence(group))
        for display, group in groups.values()
        if len(group.root_session_ids) >= 2
    ]
    return tuple(sorted_patterns(patterns, filters.limit, lambda row: row.command))


def failed_tool_result_patterns(
    connection: duckdb.DuckDBPyConnection,
    filters: TrendFilters,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
) -> tuple[FailedToolResultPattern, ...]:
    rows = connection.execute(
        """
        SELECT
            tr.session_id,
            COALESCE(NULLIF(tc.name, ''), 'unknown') AS tool_name,
            tr.output_hash,
            tr.timestamp
        FROM tool_results AS tr
        LEFT JOIN tool_calls AS tc ON tc.tool_call_id = tr.tool_call_id
        WHERE tr.is_error = TRUE AND tr.output_hash IS NOT NULL
        """
    ).fetchall()
    groups: dict[tuple[str, str], PatternGroup] = {}
    for session_id, tool_name, output_hash, timestamp in rows:
        member_id = str(session_id)
        if member_id not in eligible_roots:
            continue
        identity = (str(tool_name), str(output_hash))
        group = groups.setdefault(identity, PatternGroup())
        add_pattern_event(group, member_id, timestamp, topology, eligible_roots, filters)
    patterns = [
        FailedToolResultPattern(
            tool_name=tool_name,
            fingerprint_id=stable_id("failed_tool_result", tool_name, output_hash),
            evidence=pattern_evidence(group),
        )
        for (tool_name, output_hash), group in groups.items()
        if len(group.root_session_ids) >= 2
    ]
    return tuple(
        sorted_patterns(
            patterns,
            filters.limit,
            lambda row: f"{row.tool_name}:{row.fingerprint_id}",
        )
    )


def problematic_file_patterns(
    connection: duckdb.DuckDBPyConnection,
    filters: TrendFilters,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
    risky_session_ids: set[str],
) -> tuple[ProblematicFilePattern, ...]:
    placeholders = ", ".join("?" for _ in MUTATING_FILE_OPERATIONS)
    rows = connection.execute(
        f"""
        SELECT
            f.session_id,
            f.normalized_path,
            f.canonical_path,
            f.project_relative_path,
            COALESCE(NULLIF(s.project_path, ''), NULLIF(s.cwd, '')) AS project_path,
            f.timestamp
        FROM file_activities AS f
        JOIN sessions AS s ON s.session_id = f.session_id
        WHERE lower(f.operation) IN ({placeholders})
        """,
        list(MUTATING_FILE_OPERATIONS),
    ).fetchall()
    groups: dict[str, PatternGroup] = {}
    for session_id, _, canonical_path, relative_path, project_path, timestamp in rows:
        member_id = str(session_id)
        if member_id not in eligible_roots or member_id not in risky_session_ids:
            continue
        resolved_path = recurrence_file_path(canonical_path, relative_path, project_path)
        if resolved_path is None:
            continue
        group = groups.setdefault(resolved_path, PatternGroup())
        add_pattern_event(group, member_id, timestamp, topology, eligible_roots, filters)
    patterns = [
        ProblematicFilePattern(path=path, evidence=pattern_evidence(group))
        for path, group in groups.items()
        if len(group.root_session_ids) >= 2
    ]
    return tuple(sorted_patterns(patterns, filters.limit, lambda row: row.path))


def recurrence_file_path(
    canonical_path: object,
    relative_path: object,
    project_path: object,
) -> str | None:
    if canonical_path:
        return posixpath.normpath(str(canonical_path))
    if relative_path and project_path:
        return posixpath.normpath(posixpath.join(str(project_path), str(relative_path)))
    return None


def add_pattern_event(
    group: PatternGroup,
    member_id: str,
    timestamp: object,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
    filters: TrendFilters,
) -> None:
    root_id = eligible_roots[member_id]
    member = topology[member_id]
    root = topology[root_id]
    group.event_count += 1
    group.session_ids.add(member_id)
    group.root_session_ids.add(root_id)
    group.agents.add(member.agent_name)
    if member.is_sidechain:
        group.sidechain_session_ids.add(member_id)
    else:
        group.top_level_session_ids.add(member_id)
    if root.started_at is not None:
        group.active_buckets.add(bucket_start(root.started_at, filters.bucket))
    if isinstance(timestamp, datetime):
        group.timestamps.append(timestamp)


def pattern_evidence(group: PatternGroup) -> RecurrenceEvidence:
    return RecurrenceEvidence(
        event_count=group.event_count,
        session_count=len(group.session_ids),
        root_family_count=len(group.root_session_ids),
        top_level_session_count=len(group.top_level_session_ids),
        sidechain_session_count=len(group.sidechain_session_ids),
        agents=tuple(sorted(group.agents)),
        active_bucket_count=len(group.active_buckets),
        first_at=min(group.timestamps, default=None),
        most_recent_at=max(group.timestamps, default=None),
        example_session_id=min(group.session_ids),
    )


def sorted_patterns[PatternRowType: PatternRow](
    rows: list[PatternRowType],
    limit: int,
    stable_key: Callable[[PatternRowType], str],
) -> list[PatternRowType]:
    rows.sort(key=stable_key)
    rows.sort(key=lambda row: recency_rank(row.evidence.most_recent_at), reverse=True)
    rows.sort(key=lambda row: row.evidence.event_count, reverse=True)
    rows.sort(key=lambda row: row.evidence.session_count, reverse=True)
    rows.sort(key=lambda row: row.evidence.root_family_count, reverse=True)
    return rows[:limit]


def recency_rank(value: datetime | None) -> tuple[int, int, int, int, int, int, int, int]:
    if value is None:
        return (0, 0, 0, 0, 0, 0, 0, 0)
    return (
        1,
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
    )
