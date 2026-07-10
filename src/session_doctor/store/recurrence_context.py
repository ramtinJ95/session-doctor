from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import duckdb

from session_doctor.diagnostic_models import (
    DiagnosticFailedCommandPattern,
    DiagnosticFailedToolPattern,
    DiagnosticProblematicFilePattern,
    DiagnosticRecurrenceContext,
    NormalizedSessionData,
    RecurrenceAnalysisExclusions,
    RecurrenceEvidence,
    RecurrenceFamilyExclusions,
    RecurrenceTemporalExclusions,
)
from session_doctor.ids import stable_id
from session_doctor.privacy import (
    display_file_path,
    public_fingerprint,
    redact_command_for_display,
    redact_home,
)

from .aggregate_queries import (
    MUTATING_FILE_OPERATIONS,
    failed_command_predicate,
    latest_analysis_sql,
)
from .analysis_readers import AnalysisCompatibility, analysis_compatibility
from .pattern_readers import (
    TopologySession,
    latest_problematic_session_ids,
    recurrence_file_path,
    resolve_family,
    topology_sessions,
)


@dataclass
class _PatternGroup:
    event_count: int = 0
    selected_event_count: int = 0
    session_ids: set[str] = field(default_factory=set)
    root_ids: set[str] = field(default_factory=set)
    top_level_ids: set[str] = field(default_factory=set)
    sidechain_ids: set[str] = field(default_factory=set)
    agents: set[str] = field(default_factory=set)
    timestamps: list[datetime] = field(default_factory=list)


def load_recurrence_context(
    connection: duckdb.DuckDBPyConnection,
    normalized: NormalizedSessionData,
    compatibility: AnalysisCompatibility,
) -> DiagnosticRecurrenceContext:
    session = normalized.session
    scope_path, scope_source = preferred_hint(session.project_path, session.cwd)
    cutoff = evidence_cutoff(normalized)
    unavailable_reason = None
    if scope_path is None:
        unavailable_reason = "no_project_hint"
    elif cutoff is None or session.started_at is None:
        unavailable_reason = "untimed_session"
    topology = topology_sessions(connection)
    selected_resolution = resolve_family(session.session_id, topology)
    if unavailable_reason is None and selected_resolution.exclusion_reason is not None:
        unavailable_reason = selected_resolution.exclusion_reason
    if unavailable_reason is not None or scope_path is None or cutoff is None:
        return unavailable_context(
            unavailable_reason or "untimed_session",
            scope_path,
            scope_source,
            cutoff,
            compatibility,
        )

    window_start = monday_start(cutoff) - timedelta(weeks=11)
    hints = load_preferred_hints(connection)
    root_id = selected_resolution.root_session_id
    if root_id is None:
        return unavailable_context(
            "orphan_parent", scope_path, scope_source, cutoff, compatibility, window_start
        )
    if not hint_in_scope(hints.get(root_id), scope_path):
        return unavailable_context(
            "root_outside_project_scope",
            scope_path,
            scope_source,
            cutoff,
            compatibility,
            window_start,
        )

    matching_ids = {
        session_id for session_id, hint in hints.items() if hint_in_scope(hint, scope_path)
    }
    temporal = temporal_session_exclusions(matching_ids, topology, window_start, cutoff)
    timed_ids = {
        session_id
        for session_id in matching_ids
        if (started_at := topology[session_id].started_at) is not None
        and window_start <= started_at <= cutoff
    }
    resolutions = {session_id: resolve_family(session_id, topology) for session_id in timed_ids}
    family_exclusions = RecurrenceFamilyExclusions(
        orphan_parent=sum(row.exclusion_reason == "orphan_parent" for row in resolutions.values()),
        cycle=sum(row.exclusion_reason == "cycle" for row in resolutions.values()),
        cross_agent_parent=sum(
            row.exclusion_reason == "cross_agent_parent" for row in resolutions.values()
        ),
    )
    eligible_roots = {
        session_id: resolution.root_session_id
        for session_id, resolution in resolutions.items()
        if resolution.root_session_id is not None and resolution.root_session_id in timed_ids
    }
    event_exclusions = event_temporal_exclusions(connection, matching_ids, window_start, cutoff)
    failed_commands = failed_command_patterns(
        connection,
        session.session_id,
        topology,
        eligible_roots,
        window_start,
        cutoff,
    )
    failed_tools = failed_tool_patterns(
        connection,
        session.session_id,
        topology,
        eligible_roots,
        window_start,
        cutoff,
    )
    analysis_versions = latest_analysis_versions(connection, timed_ids)
    analysis_exclusions = RecurrenceAnalysisExclusions(
        stale=sum(
            analysis_compatibility(analysis_versions.get(session_id)) is AnalysisCompatibility.STALE
            for session_id in timed_ids
        ),
        missing=sum(
            analysis_compatibility(analysis_versions.get(session_id))
            is AnalysisCompatibility.MISSING
            for session_id in timed_ids
        ),
    )
    problematic_status = "available"
    problematic_reason = None
    problematic_files: tuple[DiagnosticProblematicFilePattern, ...] = ()
    if compatibility is not AnalysisCompatibility.CURRENT:
        problematic_status = "unavailable"
        problematic_reason = "selected_analysis_not_current"
    else:
        problematic_files = problematic_file_patterns(
            connection,
            session.session_id,
            topology,
            eligible_roots,
            latest_problematic_session_ids(connection),
            window_start,
            cutoff,
        )
    return DiagnosticRecurrenceContext(
        status="available",
        reason=None,
        scope_path=redact_home(scope_path),
        scope_source=scope_source,
        window_start=window_start,
        evidence_cutoff=cutoff,
        family_exclusions=family_exclusions,
        temporal_exclusions=RecurrenceTemporalExclusions(
            untimed_sessions=temporal.untimed_sessions,
            before_window_sessions=temporal.before_window_sessions,
            after_cutoff_sessions=temporal.after_cutoff_sessions,
            untimed_events=event_exclusions.untimed_events,
            before_window_events=event_exclusions.before_window_events,
            after_cutoff_events=event_exclusions.after_cutoff_events,
        ),
        problematic_file_analysis_exclusions=analysis_exclusions,
        problematic_files_status=problematic_status,
        problematic_files_reason=problematic_reason,
        failed_commands=failed_commands,
        failed_tool_results=failed_tools,
        problematic_files=problematic_files,
    )


def unavailable_context(
    reason: str,
    scope_path: str | None,
    scope_source: str | None,
    cutoff: datetime | None,
    compatibility: AnalysisCompatibility,
    window_start: datetime | None = None,
) -> DiagnosticRecurrenceContext:
    current = compatibility is AnalysisCompatibility.CURRENT
    return DiagnosticRecurrenceContext(
        status="unavailable",
        reason=reason,
        scope_path=redact_home(scope_path) if scope_path is not None else None,
        scope_source=scope_source,
        window_start=window_start,
        evidence_cutoff=cutoff,
        family_exclusions=RecurrenceFamilyExclusions(),
        temporal_exclusions=RecurrenceTemporalExclusions(),
        problematic_file_analysis_exclusions=RecurrenceAnalysisExclusions(),
        problematic_files_status="available" if current else "unavailable",
        problematic_files_reason=None if current else "selected_analysis_not_current",
        failed_commands=(),
        failed_tool_results=(),
        problematic_files=(),
    )


def preferred_hint(project_path: str | None, cwd: str | None) -> tuple[str | None, str | None]:
    if project_path and project_path.strip():
        return project_path, "session_project_path"
    if cwd and cwd.strip():
        return cwd, "session_cwd"
    return None, None


def load_preferred_hints(connection: duckdb.DuckDBPyConnection) -> dict[str, str | None]:
    rows = connection.execute(
        """
        SELECT session_id,
            COALESCE(NULLIF(trim(project_path), ''), NULLIF(trim(cwd), ''))
        FROM sessions
        ORDER BY session_id
        """
    ).fetchall()
    return {str(row[0]): str(row[1]) if row[1] is not None else None for row in rows}


def hint_in_scope(candidate: str | None, scope: str) -> bool:
    if candidate is None:
        return False
    normalized_scope = scope.rstrip("/") or "/"
    prefix = "/" if normalized_scope == "/" else f"{normalized_scope}/"
    return candidate == normalized_scope or candidate.startswith(prefix)


def evidence_cutoff(normalized: NormalizedSessionData) -> datetime | None:
    timestamps = [
        value
        for value in (
            *(row.timestamp for row in normalized.raw_events),
            *(row.timestamp for row in normalized.messages),
            *(row.timestamp for row in normalized.tool_calls),
            *(row.timestamp for row in normalized.tool_results),
            *(row.started_at for row in normalized.command_runs),
            *(row.ended_at for row in normalized.command_runs),
            *(row.timestamp for row in normalized.file_activities),
            *(row.timestamp for row in normalized.model_usage),
        )
        if value is not None
    ]
    return max(timestamps, default=normalized.session.ended_at or normalized.session.started_at)


def monday_start(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=value.weekday()
    )


def temporal_session_exclusions(
    matching_ids: set[str],
    topology: dict[str, TopologySession],
    window_start: datetime,
    cutoff: datetime,
) -> RecurrenceTemporalExclusions:
    starts = [topology[session_id].started_at for session_id in matching_ids]
    return RecurrenceTemporalExclusions(
        untimed_sessions=sum(value is None for value in starts),
        before_window_sessions=sum(value is not None and value < window_start for value in starts),
        after_cutoff_sessions=sum(value is not None and value > cutoff for value in starts),
    )


def latest_analysis_versions(
    connection: duckdb.DuckDBPyConnection,
    session_ids: set[str],
) -> dict[str, str]:
    if not session_ids:
        return {}
    rows = connection.execute(
        f"SELECT session_id, analyzer_version FROM ({latest_analysis_sql()})"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows if str(row[0]) in session_ids}


def failed_command_patterns(
    connection: duckdb.DuckDBPyConnection,
    selected_session_id: str,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
    window_start: datetime,
    cutoff: datetime,
) -> tuple[DiagnosticFailedCommandPattern, ...]:
    rows = connection.execute(
        f"""
        SELECT command_identity_hash, command_display, session_id,
            COALESCE(ended_at, started_at)
        FROM command_runs
        WHERE {failed_command_predicate("command_runs")}
        ORDER BY command_identity_hash, session_id, command_run_id
        """
    ).fetchall()
    groups: dict[str, tuple[str, _PatternGroup]] = {}
    for identity, display, session_id, timestamp in rows:
        member_id = str(session_id)
        if member_id not in eligible_roots or not event_in_window(timestamp, window_start, cutoff):
            continue
        display_value, group = groups.setdefault(
            str(identity), (redact_command_for_display(str(display)), _PatternGroup())
        )
        add_event(group, member_id, timestamp, selected_session_id, topology, eligible_roots)
        groups[str(identity)] = (display_value, group)
    patterns = tuple(
        DiagnosticFailedCommandPattern(
            pattern_id=stable_id("report-failed-command", identity),
            command_display=display,
            evidence=group_evidence(group),
        )
        for identity, (display, group) in groups.items()
        if recurring_for_selected(group)
    )
    return sort_patterns(patterns, lambda row: row.command_display)


def failed_tool_patterns(
    connection: duckdb.DuckDBPyConnection,
    selected_session_id: str,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
    window_start: datetime,
    cutoff: datetime,
) -> tuple[DiagnosticFailedToolPattern, ...]:
    rows = connection.execute(
        """
        SELECT tr.session_id, COALESCE(NULLIF(tc.name, ''), 'unknown'),
            tr.output_hash, tr.timestamp
        FROM tool_results AS tr
        LEFT JOIN tool_calls AS tc ON tc.tool_call_id = tr.tool_call_id
        WHERE tr.is_error = TRUE AND tr.output_hash IS NOT NULL
        ORDER BY tr.session_id, tr.tool_result_id
        """
    ).fetchall()
    groups: dict[tuple[str, str], _PatternGroup] = {}
    for session_id, tool_name, output_hash, timestamp in rows:
        member_id = str(session_id)
        if member_id not in eligible_roots or not event_in_window(timestamp, window_start, cutoff):
            continue
        identity = (str(tool_name), str(output_hash))
        add_event(
            groups.setdefault(identity, _PatternGroup()),
            member_id,
            timestamp,
            selected_session_id,
            topology,
            eligible_roots,
        )
    patterns = tuple(
        DiagnosticFailedToolPattern(
            pattern_id=stable_id("report-failed-tool", tool_name, output_hash),
            tool_name=tool_name,
            fingerprint=public_fingerprint("tool-result", f"{tool_name}\x1f{output_hash}"),
            evidence=group_evidence(group),
        )
        for (tool_name, output_hash), group in groups.items()
        if recurring_for_selected(group)
    )
    return sort_patterns(patterns, lambda row: f"{row.tool_name}:{row.fingerprint}")


def problematic_file_patterns(
    connection: duckdb.DuckDBPyConnection,
    selected_session_id: str,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
    risky_session_ids: set[str],
    window_start: datetime,
    cutoff: datetime,
) -> tuple[DiagnosticProblematicFilePattern, ...]:
    placeholders = ", ".join("?" for _ in MUTATING_FILE_OPERATIONS)
    rows = connection.execute(
        f"""
        SELECT f.session_id, f.normalized_path, f.canonical_path,
            f.project_relative_path,
            COALESCE(NULLIF(s.project_path, ''), NULLIF(s.cwd, '')), f.timestamp
        FROM file_activities AS f
        JOIN sessions AS s ON s.session_id = f.session_id
        WHERE lower(f.operation) IN ({placeholders})
        ORDER BY f.session_id, f.file_activity_id
        """,
        list(MUTATING_FILE_OPERATIONS),
    ).fetchall()
    groups: dict[str, tuple[str, _PatternGroup]] = {}
    for session_id, normalized_path, canonical_path, relative_path, project_path, timestamp in rows:
        member_id = str(session_id)
        if (
            member_id not in eligible_roots
            or member_id not in risky_session_ids
            or not event_in_window(timestamp, window_start, cutoff)
        ):
            continue
        identity = recurrence_file_path(canonical_path, relative_path, project_path)
        if identity is None:
            continue
        display, group = groups.setdefault(
            identity,
            (
                display_file_path(
                    project_relative_path=str(relative_path) if relative_path else None,
                    normalized_path=str(normalized_path),
                    canonical_path=str(canonical_path) if canonical_path else None,
                ),
                _PatternGroup(),
            ),
        )
        add_event(group, member_id, timestamp, selected_session_id, topology, eligible_roots)
        groups[identity] = (display, group)
    patterns = tuple(
        DiagnosticProblematicFilePattern(
            pattern_id=stable_id("report-problematic-file", identity),
            display_path=display,
            evidence=group_evidence(group),
        )
        for identity, (display, group) in groups.items()
        if recurring_for_selected(group)
    )
    return sort_patterns(patterns, lambda row: row.display_path)


def event_temporal_exclusions(
    connection: duckdb.DuckDBPyConnection,
    matching_ids: set[str],
    window_start: datetime,
    cutoff: datetime,
) -> RecurrenceTemporalExclusions:
    placeholders = ", ".join("?" for _ in MUTATING_FILE_OPERATIONS)
    rows = connection.execute(
        f"""
        SELECT session_id, COALESCE(ended_at, started_at) FROM command_runs
        WHERE {failed_command_predicate("command_runs")}
        UNION ALL
        SELECT session_id, timestamp FROM tool_results
        WHERE is_error = TRUE AND output_hash IS NOT NULL
        UNION ALL
        SELECT session_id, timestamp FROM file_activities
        WHERE lower(operation) IN ({placeholders})
        """,
        list(MUTATING_FILE_OPERATIONS),
    ).fetchall()
    timestamps = [row[1] for row in rows if str(row[0]) in matching_ids]
    return RecurrenceTemporalExclusions(
        untimed_events=sum(not isinstance(value, datetime) for value in timestamps),
        before_window_events=sum(
            isinstance(value, datetime) and value < window_start for value in timestamps
        ),
        after_cutoff_events=sum(
            isinstance(value, datetime) and value > cutoff for value in timestamps
        ),
    )


def event_in_window(timestamp: object, window_start: datetime, cutoff: datetime) -> bool:
    return isinstance(timestamp, datetime) and window_start <= timestamp <= cutoff


def add_event(
    group: _PatternGroup,
    session_id: str,
    timestamp: object,
    selected_session_id: str,
    topology: dict[str, TopologySession],
    eligible_roots: dict[str, str],
) -> None:
    member = topology[session_id]
    group.event_count += 1
    group.selected_event_count += session_id == selected_session_id
    group.session_ids.add(session_id)
    group.root_ids.add(eligible_roots[session_id])
    group.agents.add(member.agent_name)
    if member.is_sidechain:
        group.sidechain_ids.add(session_id)
    else:
        group.top_level_ids.add(session_id)
    if isinstance(timestamp, datetime):
        group.timestamps.append(timestamp)


def recurring_for_selected(group: _PatternGroup) -> bool:
    return group.selected_event_count > 0 and len(group.root_ids) >= 2


def group_evidence(group: _PatternGroup) -> RecurrenceEvidence:
    return RecurrenceEvidence(
        event_count=group.event_count,
        selected_session_event_count=group.selected_event_count,
        session_count=len(group.session_ids),
        root_family_count=len(group.root_ids),
        top_level_session_count=len(group.top_level_ids),
        sidechain_session_count=len(group.sidechain_ids),
        agents=tuple(sorted(group.agents)),
        first_at=min(group.timestamps),
        most_recent_at=max(group.timestamps),
    )


def sort_patterns[Pattern](rows: tuple[Pattern, ...], display_key) -> tuple[Pattern, ...]:
    ordered = sorted(rows, key=lambda row: (display_key(row), row.pattern_id))
    ordered.sort(key=lambda row: row.evidence.most_recent_at, reverse=True)
    ordered.sort(key=lambda row: row.evidence.event_count, reverse=True)
    ordered.sort(key=lambda row: row.evidence.session_count, reverse=True)
    ordered.sort(key=lambda row: row.evidence.root_family_count, reverse=True)
    return tuple(ordered)
