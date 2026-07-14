from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import duckdb

from session_doctor.adapters import built_in_adapters
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    AgentName,
    DelegationStatus,
    EpisodeAggregateEligibility,
    EpisodeAnalysis,
    EpisodeDelegation,
    EpisodeEntityMembership,
    EpisodeKind,
    EpisodeMembershipStatus,
    EpisodeTopologyProjection,
    EpisodeUnavailableChild,
    SemanticAnalysisComponents,
    TaskEpisode,
)
from session_doctor.segmentation import SEGMENTATION_VERSION, segment_session
from session_doctor.semantic_foundations import ORDERING_VERSION
from session_doctor.store import DuckDBStore
from session_doctor.store.connection import transaction, write_connection
from session_doctor.store.json_values import parse_metadata
from session_doctor.store.lifecycle import FINALIZED_LIFECYCLE_STATES, LifecycleObservation
from session_doctor.store.normalization_runs import (
    NORMALIZATION_CONFIGURATION_HASH,
    NORMALIZATION_VERSION,
    StoredNormalization,
    canonical_model_json,
    load_normalization_from_connection,
    normalization_configuration_hash,
    parser_version_key,
    versions_compatible,
)
from session_doctor.store.semantic_runs import record_semantic_analysis_run_rows

EPISODE_ANALYSIS_SCHEMA_VERSION = "episode-analysis-v2"
DELEGATION_TOPOLOGY_VERSION = "delegation-topology-v1"
MEMBERSHIP_POLICY_VERSION = "episode-membership-v1"
UNAVAILABLE_RELATION_VERSION = "relations-unavailable-pr10"
UNAVAILABLE_RESULT_VERSION = "results-unavailable-pr13"
UNAVAILABLE_FINDING_VERSION = "findings-unavailable-pr15"
UNAVAILABLE_FACET_VERSION = "facets-unavailable-pr17"


class EpisodeAnalysisUnavailable(ValueError):
    pass


class EpisodePersistenceConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalysisInput:
    stored: StoredNormalization
    lifecycle: LifecycleObservation
    lifecycle_policy_version: str


def analyze_session_episodes(
    _store: DuckDBStore,
    session_id: str,
    database_path: Path,
) -> EpisodeAnalysis:
    with write_connection(database_path) as connection, transaction(connection):
        cache: dict[str, tuple[EpisodeAnalysis, AnalysisInput]] = {}
        analysis, _ = _analyze_session(connection, session_id, cache, ())
        return analysis


def _analyze_session(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
    cache: dict[str, tuple[EpisodeAnalysis, AnalysisInput]],
    stack: tuple[str, ...],
) -> tuple[EpisodeAnalysis, AnalysisInput]:
    if session_id in cache:
        return cache[session_id]
    if session_id in stack:
        raise EpisodeAnalysisUnavailable("delegation topology contains a session cycle")

    selected = _select_analysis_input(connection, session_id)
    segmented = segment_session(selected.stored.bundle, selected.lifecycle)
    episodes = _materialize_source_episodes(segmented, selected)
    topology_configuration = _delegation_configuration_identity(
        connection,
        selected,
        cache,
        (*stack, session_id),
    )
    components = SemanticAnalysisComponents(
        normalization_run_id=selected.stored.run.normalization_run_id,
        lifecycle_observation_id=selected.lifecycle.lifecycle_observation_id,
        lifecycle_policy_version=selected.lifecycle_policy_version,
        ordering_version=ORDERING_VERSION,
        segmentation_version=SEGMENTATION_VERSION,
        relation_rule_set_version=UNAVAILABLE_RELATION_VERSION,
        result_rule_set_version=UNAVAILABLE_RESULT_VERSION,
        finding_rule_set_version=UNAVAILABLE_FINDING_VERSION,
        facet_policy_version=UNAVAILABLE_FACET_VERSION,
        configuration_hash=stable_id(
            "episode-analysis-configuration",
            selected.stored.run.configuration_hash,
            MEMBERSHIP_POLICY_VERSION,
            DELEGATION_TOPOLOGY_VERSION,
            topology_configuration,
        ),
    )
    semantic_run = record_semantic_analysis_run_rows(
        connection,
        components,
        metadata={
            "episode_analysis_schema_version": EPISODE_ANALYSIS_SCHEMA_VERSION,
            "downstream_status": "unavailable",
        },
    )
    analysis_identity = semantic_run.analysis_identity
    episodes, delegations = _resolve_delegation(
        connection,
        selected,
        episodes,
        analysis_identity,
        cache,
        (*stack, session_id),
    )
    memberships = _derive_memberships(
        connection,
        selected.stored,
        analysis_identity,
        episodes,
    )
    analysis = EpisodeAnalysis(
        schema_version=EPISODE_ANALYSIS_SCHEMA_VERSION,
        analysis_identity=analysis_identity,
        normalization_run_id=selected.stored.run.normalization_run_id,
        segmentation_version=SEGMENTATION_VERSION,
        session_id=session_id,
        lifecycle_observation_id=selected.lifecycle.lifecycle_observation_id,
        lifecycle_state=selected.lifecycle.state,
        episodes=episodes,
        boundaries=segmented.boundaries,
        observations=segmented.observations,
        entity_memberships=memberships,
        delegations=delegations,
    )
    persisted = _persist_and_load(connection, analysis)
    cache[session_id] = (persisted, selected)
    if selected.stored.bundle.session is not None:
        child_rows = connection.execute(
            """
            SELECT session_id FROM sessions
            WHERE parent_session_id = ? AND is_sidechain
            ORDER BY session_id
            """,
            [session_id],
        ).fetchall()
        unavailable_children: list[EpisodeUnavailableChild] = []
        for child_row in child_rows:
            child_session_id = str(child_row[0])
            child_snapshot_id, child_logical_source_id = _child_capture_reference(
                connection, child_session_id
            )
            if child_session_id in (*stack, session_id):
                unavailable_children.append(
                    EpisodeUnavailableChild(
                        child_session_id=child_session_id,
                        reason="native_parent_cycle",
                        snapshot_id=child_snapshot_id,
                        logical_source_id=child_logical_source_id,
                    )
                )
                continue
            try:
                child_analysis, _ = _analyze_session(
                    connection,
                    child_session_id,
                    cache,
                    (*stack, session_id),
                )
            except EpisodeAnalysisUnavailable as exc:
                unavailable_children.append(
                    EpisodeUnavailableChild(
                        child_session_id=child_session_id,
                        reason=f"analysis_unavailable:{exc}",
                        snapshot_id=child_snapshot_id,
                        logical_source_id=child_logical_source_id,
                    )
                )
            else:
                has_parent_topology = any(
                    row.parent_session_id == session_id for row in child_analysis.delegations
                )
                if not has_parent_topology:
                    unavailable_children.append(
                        EpisodeUnavailableChild(
                            child_session_id=child_session_id,
                            reason=(
                                "child_has_no_episode_delegation"
                                if not child_analysis.episodes
                                else "child_parent_topology_unavailable"
                            ),
                            snapshot_id=child_snapshot_id,
                            logical_source_id=child_logical_source_id,
                        )
                    )
        topology_delegations = [
            EpisodeDelegation.model_validate_json(str(row[0]))
            for row in connection.execute(
                """
                SELECT payload_json FROM episode_delegations
                WHERE child_analysis_identity = ?
                   OR parent_analysis_identity = ?
                   OR parent_session_id = ?
                ORDER BY child_analysis_identity, delegation_order, delegation_id
                """,
                [analysis_identity, analysis_identity, session_id],
            ).fetchall()
        ]
        topology_projection = _persist_topology_projection(
            connection,
            analysis_identity,
            topology_delegations,
            unavailable_children,
        )
        persisted = persisted.model_copy(update={"topology_projection": topology_projection})
        cache[session_id] = (persisted, selected)
    return persisted, selected


def _persist_topology_projection(
    connection: duckdb.DuckDBPyConnection,
    analysis_identity: str,
    delegations: list[EpisodeDelegation],
    unavailable_children: list[EpisodeUnavailableChild],
) -> EpisodeTopologyProjection:
    ordered_delegations = sorted(delegations, key=lambda row: row.delegation_id)
    unavailable_by_session = {row.child_session_id: row for row in unavailable_children}
    ordered_unavailable = [unavailable_by_session[key] for key in sorted(unavailable_by_session)]
    projection = EpisodeTopologyProjection(
        topology_projection_id=stable_id(
            "episode-topology-projection",
            DELEGATION_TOPOLOGY_VERSION,
            analysis_identity,
            *(row.delegation_id for row in ordered_delegations),
            *(
                f"{row.child_session_id}:{row.reason}:{row.snapshot_id}:{row.logical_source_id}"
                for row in ordered_unavailable
            ),
        ),
        topology_version=DELEGATION_TOPOLOGY_VERSION,
        analysis_identity=analysis_identity,
        delegations=ordered_delegations,
        unavailable_children=ordered_unavailable,
    )
    values = (
        projection.topology_projection_id,
        projection.topology_version,
        projection.analysis_identity,
        canonical_model_json(projection),
    )
    connection.execute(
        "INSERT INTO episode_topology_projections "
        "(topology_projection_id, topology_version, analysis_identity, payload_json) "
        "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        list(values),
    )
    stored = connection.execute(
        "SELECT * EXCLUDE (created_at) FROM episode_topology_projections "
        "WHERE topology_projection_id = ?",
        [projection.topology_projection_id],
    ).fetchone()
    if stored is None or stored != values:
        raise EpisodePersistenceConflict("episode topology projection identity collision")
    if ordered_delegations:
        connection.executemany(
            "INSERT INTO episode_topology_projection_delegations VALUES (?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            [
                (projection.topology_projection_id, row.delegation_id, row_order)
                for row_order, row in enumerate(ordered_delegations)
            ],
        )
    stored_links = connection.execute(
        "SELECT delegation_id FROM episode_topology_projection_delegations "
        "WHERE topology_projection_id = ? ORDER BY delegation_order",
        [projection.topology_projection_id],
    ).fetchall()
    if stored_links != [(row.delegation_id,) for row in ordered_delegations]:
        raise EpisodePersistenceConflict("episode topology projection is incomplete")
    if ordered_unavailable:
        connection.executemany(
            "INSERT INTO episode_topology_projection_unavailable_children "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [
                (
                    projection.topology_projection_id,
                    row.child_session_id,
                    row.reason,
                    row.snapshot_id,
                    row.logical_source_id,
                    row_order,
                )
                for row_order, row in enumerate(ordered_unavailable)
            ],
        )
    stored_unavailable = connection.execute(
        "SELECT child_session_id, unavailable_reason, snapshot_id, logical_source_id "
        "FROM episode_topology_projection_unavailable_children "
        "WHERE topology_projection_id = ? ORDER BY child_order",
        [projection.topology_projection_id],
    ).fetchall()
    if stored_unavailable != [
        (row.child_session_id, row.reason, row.snapshot_id, row.logical_source_id)
        for row in ordered_unavailable
    ]:
        raise EpisodePersistenceConflict("episode unavailable-child projection is incomplete")
    return EpisodeTopologyProjection.model_validate_json(str(stored[3]))


def _child_capture_reference(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
) -> tuple[str | None, str | None]:
    row = connection.execute(
        """
        SELECT latest.snapshot_id, latest.logical_source_id
        FROM sessions
        JOIN session_sources USING (source_id)
        JOIN snapshot_bundles AS normalized_bundle USING (snapshot_bundle_id)
        JOIN source_snapshots AS normalized_snapshot
          ON normalized_snapshot.snapshot_id = normalized_bundle.primary_snapshot_id
        JOIN source_snapshots AS latest
          ON latest.logical_source_id = normalized_snapshot.logical_source_id
        WHERE sessions.session_id = ?
        ORDER BY latest.capture_sequence DESC
        LIMIT 1
        """,
        [session_id],
    ).fetchone()
    return (str(row[0]), str(row[1])) if row is not None else (None, None)


def _select_analysis_input(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
) -> AnalysisInput:
    row = connection.execute(
        """
        SELECT sources.snapshot_bundle_id, sessions.agent_name
        FROM sessions
        JOIN session_sources AS sources USING (source_id)
        WHERE sessions.session_id = ?
        """,
        [session_id],
    ).fetchone()
    if row is None or row[0] is None:
        raise EpisodeAnalysisUnavailable("session has no normalized v2 input")
    snapshot_bundle_id = str(row[0])
    latest = connection.execute(
        """
        SELECT latest_bundle.snapshot_bundle_id,
            latest_bundle.primary_snapshot_id = latest_snapshot.snapshot_id,
            latest_snapshot.snapshot_content_id = current_snapshot.snapshot_content_id
        FROM snapshot_bundles AS current_bundle
        JOIN source_snapshots AS current_snapshot
          ON current_snapshot.snapshot_id = current_bundle.primary_snapshot_id
        JOIN source_snapshots AS latest_snapshot
          ON latest_snapshot.logical_source_id = current_snapshot.logical_source_id
        LEFT JOIN snapshot_bundle_members AS latest_member
          ON latest_member.snapshot_id = latest_snapshot.snapshot_id
        LEFT JOIN snapshot_bundles AS latest_bundle
          ON latest_bundle.snapshot_bundle_id = latest_member.snapshot_bundle_id
        WHERE current_bundle.snapshot_bundle_id = ?
        ORDER BY latest_snapshot.capture_sequence DESC
        LIMIT 1
        """,
        [snapshot_bundle_id],
    ).fetchone()
    latest_is_current_primary = (
        latest is not None
        and latest[0] is not None
        and bool(latest[1])
        and str(latest[0]) == snapshot_bundle_id
    )
    latest_is_equivalent_member = (
        latest is not None and latest[0] is not None and not bool(latest[1]) and bool(latest[2])
    )
    if not latest_is_current_primary and not latest_is_equivalent_member:
        raise EpisodeAnalysisUnavailable("latest capture has no current normalized projection")

    agent_name = AgentName(str(row[1]))
    adapter = next(item for item in built_in_adapters() if item.name is agent_name)
    expected_configuration = normalization_configuration_hash(
        NORMALIZATION_CONFIGURATION_HASH,
        adapter.capabilities,
    )
    run_rows = connection.execute(
        """
        SELECT runs.normalization_run_id, runs.adapter_name,
            runs.adapter_version, runs.normalization_version,
            runs.configuration_hash
        FROM normalization_run_bundles AS links
        JOIN normalization_runs AS runs USING (normalization_run_id)
        WHERE links.snapshot_bundle_id = ?
        """,
        [snapshot_bundle_id],
    ).fetchall()
    ordered_runs = sorted(
        run_rows,
        key=lambda run: (parser_version_key(str(run[2])) or (-1,), str(run[0])),
        reverse=True,
    )
    selected_run_id = next(
        (
            str(run[0])
            for run in ordered_runs
            if run[1:]
            == (
                adapter.name.value,
                adapter.version,
                NORMALIZATION_VERSION,
                expected_configuration,
            )
        ),
        None,
    )
    if selected_run_id is None:
        current_version = parser_version_key(adapter.version)
        selected_run_id = next(
            (
                str(run[0])
                for run in ordered_runs
                if str(run[1]) == adapter.name.value
                and str(run[3]) == NORMALIZATION_VERSION
                and str(run[4]) == expected_configuration
                and versions_compatible(
                    parser_version_key(str(run[2])),
                    current_version,
                    str(run[2]),
                    adapter.version,
                )
            ),
            None,
        )
    if selected_run_id is None:
        raise EpisodeAnalysisUnavailable("normalization input is unavailable")
    stored = load_normalization_from_connection(
        connection,
        selected_run_id,
        snapshot_bundle_id,
    )
    lifecycle_row = connection.execute(
        """
        SELECT lifecycle_observation_id, snapshot_bundle_id,
            lifecycle_policy_version, state, observed_at, evidence_json
        FROM lifecycle_observations
        WHERE snapshot_bundle_id = ?
        """,
        [snapshot_bundle_id],
    ).fetchone()
    if stored is None or lifecycle_row is None:
        raise EpisodeAnalysisUnavailable("lifecycle or normalization input is unavailable")
    lifecycle = LifecycleObservation(
        lifecycle_observation_id=str(lifecycle_row[0]),
        snapshot_bundle_id=str(lifecycle_row[1]),
        state=str(lifecycle_row[3]),
        observed_at=lifecycle_row[4],
        evidence=parse_metadata(lifecycle_row[5]),
    )
    return AnalysisInput(stored, lifecycle, str(lifecycle_row[2]))


def _materialize_source_episodes(
    segmented: EpisodeAnalysis,
    selected: AnalysisInput,
) -> list[TaskEpisode]:
    session = selected.stored.bundle.session
    if session is None:
        return []
    if segmented.episodes:
        return [
            episode.model_copy(
                update={
                    "rollup_owner_episode_id": episode.episode_id,
                    "episode_kind": (
                        EpisodeKind.DELEGATED if session.is_sidechain else EpisodeKind.DIRECT
                    ),
                    "aggregate_eligibility": (
                        EpisodeAggregateEligibility.INELIGIBLE_DELEGATED_CHILD
                        if session.is_sidechain
                        else EpisodeAggregateEligibility.ELIGIBLE_DIRECT
                    ),
                }
            )
            for episode in segmented.episodes
        ]
    if not session.is_sidechain or not selected.stored.bundle.raw_events:
        return []
    ordered_events = sorted(
        (
            event
            for event in selected.stored.bundle.raw_events
            if event.source_id == session.source_id
        ),
        key=lambda event: (event.record_index, event.event_id),
    )
    if not ordered_events:
        return []
    anchors = [event.event_id for event in ordered_events]
    episode_id = stable_id(
        "task-episode",
        SEGMENTATION_VERSION,
        session.session_id,
        "delegated-spawn",
        anchors[0],
        anchors[-1],
    )
    return [
        TaskEpisode(
            episode_id=episode_id,
            segmentation_version=SEGMENTATION_VERSION,
            session_id=session.session_id,
            event_anchor_ids=anchors,
            lifecycle_state=selected.lifecycle.state,
            provisional=selected.lifecycle.state not in FINALIZED_LIFECYCLE_STATES,
            episode_kind=EpisodeKind.DELEGATED,
            rollup_owner_episode_id=episode_id,
            aggregate_eligibility=(EpisodeAggregateEligibility.INELIGIBLE_DELEGATED_CHILD),
        )
    ]


def _delegation_configuration_identity(
    connection: duckdb.DuckDBPyConnection,
    selected: AnalysisInput,
    cache: dict[str, tuple[EpisodeAnalysis, AnalysisInput]],
    stack: tuple[str, ...],
) -> str:
    session = selected.stored.bundle.session
    if session is None or not session.is_sidechain:
        return stable_id("delegation-configuration", DELEGATION_TOPOLOGY_VERSION, "direct")
    sidecar = session.metadata.get("subagent_metadata")
    spawn_native_id = sidecar.get("tool_use_id") if isinstance(sidecar, dict) else None
    parent_analysis_identity = "unavailable"
    parent_snapshot_bundle_id = "unavailable"
    parent_capture_status = "unavailable"
    if (
        session.metadata.get("parent_link_status") == "linked"
        and isinstance(session.parent_session_id, str)
        and isinstance(spawn_native_id, str)
        and (session.parent_session_id not in stack or session.parent_session_id in cache)
    ):
        try:
            parent_analysis, parent_input = _analyze_session(
                connection,
                session.parent_session_id,
                cache,
                stack,
            )
        except EpisodeAnalysisUnavailable:
            pass
        else:
            parent_analysis_identity = parent_analysis.analysis_identity
            parent_snapshot_bundle_id = parent_input.stored.run.snapshot_bundle_id
            parent_capture_status = (
                "matched"
                if _parent_capture_matches(connection, selected, parent_input)
                else "mismatched"
            )
    return stable_id(
        "delegation-configuration",
        DELEGATION_TOPOLOGY_VERSION,
        session.metadata.get("parent_link_status"),
        session.parent_session_id,
        spawn_native_id,
        parent_analysis_identity,
        parent_snapshot_bundle_id,
        parent_capture_status,
    )


def _resolve_delegation(
    connection: duckdb.DuckDBPyConnection,
    selected: AnalysisInput,
    episodes: list[TaskEpisode],
    analysis_identity: str,
    cache: dict[str, tuple[EpisodeAnalysis, AnalysisInput]],
    stack: tuple[str, ...],
) -> tuple[list[TaskEpisode], list[EpisodeDelegation]]:
    session = selected.stored.bundle.session
    if session is None or not session.is_sidechain or not episodes:
        return episodes, []
    parent_session_id = session.parent_session_id
    parent_link_status = session.metadata.get("parent_link_status")
    sidecar = session.metadata.get("subagent_metadata")
    spawn_native_id = sidecar.get("tool_use_id") if isinstance(sidecar, dict) else None
    status = DelegationStatus.UNAVAILABLE
    parent_analysis: EpisodeAnalysis | None = None
    parent_input: AnalysisInput | None = None
    parent_episode_id: str | None = None
    spawn_tool_call_id: str | None = None
    spawn_event_id: str | None = None
    candidates: list[str] = []
    reason = "native_parent_or_spawn_evidence_unavailable"

    if (
        parent_link_status == "linked"
        and isinstance(parent_session_id, str)
        and isinstance(spawn_native_id, str)
        and (parent_session_id not in stack or parent_session_id in cache)
    ):
        try:
            parent_analysis, parent_input = _analyze_session(
                connection,
                parent_session_id,
                cache,
                stack,
            )
        except EpisodeAnalysisUnavailable:
            reason = "parent_analysis_input_unavailable"
        else:
            if not _parent_capture_matches(connection, selected, parent_input):
                reason = "parent_capture_does_not_match_child_topology_evidence"
            else:
                spawn_calls = [
                    call
                    for call in parent_input.stored.bundle.tool_calls
                    if call.native_tool_call_id == spawn_native_id
                ]
                membership_by_entity = {
                    row.entity_id: row for row in parent_analysis.entity_memberships
                }
                candidates = sorted(
                    {
                        membership.source_episode_id
                        for call in spawn_calls
                        if (membership := membership_by_entity.get(call.tool_call_id)) is not None
                        and membership.status is EpisodeMembershipStatus.ASSIGNED
                        and membership.source_episode_id is not None
                    }
                )
                if len(spawn_calls) == 1 and len(candidates) == 1:
                    status = DelegationStatus.LINKED
                    parent_episode_id = candidates[0]
                    spawn_tool_call_id = spawn_calls[0].tool_call_id
                    spawn_event_id = spawn_calls[0].source_event_id
                    reason = "native_spawn_tool_link"
                elif len(spawn_calls) > 1 or len(candidates) > 1:
                    status = DelegationStatus.AMBIGUOUS
                    reason = "native_spawn_evidence_ambiguous"
                else:
                    reason = "native_spawn_event_has_no_parent_episode"
    elif parent_link_status == "ambiguous":
        status = DelegationStatus.AMBIGUOUS
        reason = "native_parent_link_ambiguous"
    elif parent_session_id in stack:
        reason = "native_parent_cycle"

    parent_episode = (
        next(
            (
                episode
                for episode in parent_analysis.episodes
                if episode.episode_id == parent_episode_id
            ),
            None,
        )
        if parent_analysis is not None and parent_episode_id is not None
        else None
    )
    resolved: list[TaskEpisode] = []
    delegations: list[EpisodeDelegation] = []
    for episode in episodes:
        owner = (
            parent_episode.rollup_owner_episode_id
            if status is DelegationStatus.LINKED
            and parent_episode is not None
            and parent_episode.rollup_owner_episode_id is not None
            else episode.episode_id
        )
        resolved_episode = episode.model_copy(
            update={
                "parent_episode_id": (
                    parent_episode_id if status is DelegationStatus.LINKED else None
                ),
                "rollup_owner_episode_id": owner,
                "episode_kind": EpisodeKind.DELEGATED,
                "aggregate_eligibility": (EpisodeAggregateEligibility.INELIGIBLE_DELEGATED_CHILD),
            }
        )
        resolved.append(resolved_episode)
        delegations.append(
            EpisodeDelegation(
                delegation_id=stable_id(
                    "episode-delegation",
                    DELEGATION_TOPOLOGY_VERSION,
                    analysis_identity,
                    episode.episode_id,
                    status.value,
                    parent_episode_id or "unavailable",
                ),
                topology_version=DELEGATION_TOPOLOGY_VERSION,
                status=status,
                child_analysis_identity=analysis_identity,
                child_episode_id=episode.episode_id,
                child_session_id=session.session_id,
                parent_analysis_identity=(
                    parent_analysis.analysis_identity if parent_analysis is not None else None
                ),
                parent_episode_id=(
                    parent_episode_id if status is DelegationStatus.LINKED else None
                ),
                parent_session_id=parent_session_id,
                rollup_owner_episode_id=owner,
                spawn_tool_call_id=spawn_tool_call_id,
                spawn_event_id=spawn_event_id,
                parent_candidate_episode_ids=candidates,
                provenance={
                    "reason": reason,
                    "parent_link_status": parent_link_status,
                    "native_spawn_tool_call_id": spawn_native_id,
                    "child_snapshot_bundle_id": selected.stored.run.snapshot_bundle_id,
                    "parent_snapshot_bundle_id": (
                        parent_input.stored.run.snapshot_bundle_id
                        if parent_input is not None
                        else None
                    ),
                    "ordering": "source_local_only",
                },
            )
        )
    return resolved, delegations


def _parent_capture_matches(
    connection: duckdb.DuckDBPyConnection,
    child: AnalysisInput,
    parent: AnalysisInput,
) -> bool:
    row = connection.execute(
        """
        SELECT count(*)
        FROM snapshot_bundle_members AS child_member
        JOIN source_snapshots AS child_snapshot
          ON child_snapshot.snapshot_id = child_member.snapshot_id
        JOIN snapshot_bundles AS parent_bundle
          ON parent_bundle.snapshot_bundle_id = ?
        JOIN source_snapshots AS parent_snapshot
          ON parent_snapshot.snapshot_id = parent_bundle.primary_snapshot_id
        WHERE child_member.snapshot_bundle_id = ?
          AND child_member.logical_source_id = parent_snapshot.logical_source_id
          AND child_snapshot.blob_id = parent_snapshot.blob_id
          AND child_member.member_role IN ('related_transcript', 'subagent_transcript')
        """,
        [
            parent.stored.run.snapshot_bundle_id,
            child.stored.run.snapshot_bundle_id,
        ],
    ).fetchone()
    return row == (1,)


def _derive_memberships(
    connection: duckdb.DuckDBPyConnection,
    stored: StoredNormalization,
    analysis_identity: str,
    episodes: list[TaskEpisode],
) -> list[EpisodeEntityMembership]:
    rows = connection.execute(
        """
        SELECT entity_kind, entity_id, payload_json
        FROM normalized_entities
        WHERE normalization_run_id = ?
        ORDER BY entity_kind, entity_order, entity_id
        """,
        [stored.run.normalization_run_id],
    ).fetchall()
    episode_by_anchor = {
        anchor: episode for episode in episodes for anchor in episode.event_anchor_ids
    }
    raw_by_source_record = {
        (event.source_id, event.record_index): event.event_id for event in stored.bundle.raw_events
    }
    tool_call_by_id = {call.tool_call_id: call for call in stored.bundle.tool_calls}
    episode_by_tool_call = {
        call_id: episode_by_anchor.get(call.source_event_id or "")
        for call_id, call in tool_call_by_id.items()
    }
    memberships: list[EpisodeEntityMembership] = []
    for entity_kind, entity_id, raw_payload in rows:
        payload = json.loads(str(raw_payload))
        anchors: list[str] = []
        candidate_episodes: list[TaskEpisode] = []
        reason = "missing_event_provenance"
        if str(entity_kind) in {"session", "session_source"}:
            reason = "native_session_container"
        else:
            if str(entity_kind) == "raw_event":
                anchors.append(str(entity_id))
                if source_episode := episode_by_anchor.get(str(entity_id)):
                    candidate_episodes.append(source_episode)
                else:
                    reason = "source_event_outside_episode"
            if str(entity_kind) == "message" and payload.get("source_event_id") is None:
                anchors.append(str(entity_id))
                if source_episode := episode_by_anchor.get(str(entity_id)):
                    candidate_episodes.append(source_episode)
                else:
                    reason = "message_anchor_outside_episode"
            source_event_id = payload.get("source_event_id")
            if isinstance(source_event_id, str):
                anchors.append(source_event_id)
                if source_episode := episode_by_anchor.get(source_event_id):
                    candidate_episodes.append(source_episode)
                else:
                    reason = "source_event_outside_episode"
            if str(entity_kind) in {"tool_result", "command_run"}:
                tool_call_id = payload.get("tool_call_id")
                if isinstance(tool_call_id, str):
                    if call := tool_call_by_id.get(tool_call_id):
                        if call.source_event_id:
                            anchors.append(call.source_event_id)
                    if source_episode := episode_by_tool_call.get(tool_call_id):
                        candidate_episodes.append(source_episode)
            if str(entity_kind) == "parse_warning":
                warning_anchor = _warning_anchor(payload, raw_by_source_record)
                if warning_anchor is not None:
                    anchors.append(warning_anchor)
                    if source_episode := episode_by_anchor.get(warning_anchor):
                        candidate_episodes.append(source_episode)
                    else:
                        reason = "warning_record_outside_episode"
        unique_candidates = {episode.episode_id: episode for episode in candidate_episodes}
        ordered_candidates = [unique_candidates[key] for key in sorted(unique_candidates)]
        if len(ordered_candidates) == 1:
            source_episode = ordered_candidates[0]
            status = EpisodeMembershipStatus.ASSIGNED
            reason = "deterministic_source_provenance"
            rollup_owner = source_episode.rollup_owner_episode_id
            eligible = (
                source_episode.aggregate_eligibility is EpisodeAggregateEligibility.ELIGIBLE_DIRECT
            )
        elif len(ordered_candidates) > 1:
            source_episode = None
            status = EpisodeMembershipStatus.AMBIGUOUS
            reason = "conflicting_entity_provenance"
            rollup_owner = None
            eligible = False
        else:
            source_episode = None
            status = EpisodeMembershipStatus.UNASSIGNED
            rollup_owner = None
            eligible = False
            if not episodes and reason == "missing_event_provenance":
                reason = "session_has_no_analyzable_episode"
        memberships.append(
            EpisodeEntityMembership(
                membership_id=stable_id(
                    "episode-entity-membership",
                    MEMBERSHIP_POLICY_VERSION,
                    analysis_identity,
                    str(entity_kind),
                    str(entity_id),
                ),
                analysis_identity=analysis_identity,
                normalization_run_id=stored.run.normalization_run_id,
                entity_kind=str(entity_kind),
                entity_id=str(entity_id),
                status=status,
                reason=reason,
                source_episode_id=(
                    source_episode.episode_id if source_episode is not None else None
                ),
                rollup_owner_episode_id=rollup_owner,
                candidate_episode_ids=[episode.episode_id for episode in ordered_candidates],
                evidence_anchor_ids=list(dict.fromkeys(anchors)),
                additive_aggregate_eligible=eligible,
            )
        )
    return memberships


def _warning_anchor(
    payload: dict[str, object],
    raw_by_source_record: dict[tuple[str, int], str],
) -> str | None:
    source_id = payload.get("source_id")
    record_index = payload.get("record_index")
    if not isinstance(source_id, str) or not isinstance(record_index, int):
        return None
    return raw_by_source_record.get((source_id, record_index))


def _persist_and_load(
    connection: duckdb.DuckDBPyConnection,
    analysis: EpisodeAnalysis,
) -> EpisodeAnalysis:
    run_values = (
        analysis.analysis_identity,
        analysis.normalization_run_id,
        analysis.lifecycle_observation_id,
        analysis.segmentation_version,
        analysis.session_id,
        analysis.schema_version,
        len(analysis.episodes),
        len(analysis.boundaries),
        len(analysis.observations),
        len(analysis.entity_memberships),
        len(analysis.delegations),
    )
    connection.execute(
        """
        INSERT INTO episode_analysis_runs (
            analysis_identity, normalization_run_id, lifecycle_observation_id,
            segmentation_version, session_id, schema_version, episode_count,
            boundary_count, observation_count, membership_count, delegation_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        list(run_values),
    )
    stored_run = connection.execute(
        "SELECT * EXCLUDE (created_at) FROM episode_analysis_runs WHERE analysis_identity = ?",
        [analysis.analysis_identity],
    ).fetchone()
    if stored_run != run_values:
        raise EpisodePersistenceConflict("episode analysis identity collision")

    _insert_payload_rows(
        connection,
        "episodes",
        (
            (
                analysis.analysis_identity,
                row.episode_id,
                row_order,
                row.session_id,
                row.rollup_owner_episode_id,
                row.aggregate_eligibility.value,
                canonical_model_json(row),
            )
            for row_order, row in enumerate(analysis.episodes)
        ),
        7,
    )
    _insert_payload_rows(
        connection,
        "episode_boundaries",
        (
            (
                analysis.analysis_identity,
                row.boundary_id,
                row_order,
                canonical_model_json(row),
            )
            for row_order, row in enumerate(analysis.boundaries)
        ),
        4,
    )
    _insert_payload_rows(
        connection,
        "episode_observations",
        (
            (
                analysis.analysis_identity,
                row.observation_id,
                row_order,
                canonical_model_json(row),
            )
            for row_order, row in enumerate(analysis.observations)
        ),
        4,
    )
    _insert_payload_rows(
        connection,
        "episode_entity_memberships",
        (
            (
                row.membership_id,
                row.analysis_identity,
                row.normalization_run_id,
                row.entity_kind,
                row.entity_id,
                row_order,
                row.status.value,
                row.source_episode_id,
                row.rollup_owner_episode_id,
                row.additive_aggregate_eligible,
                canonical_model_json(row),
            )
            for row_order, row in enumerate(analysis.entity_memberships)
        ),
        11,
    )
    _insert_payload_rows(
        connection,
        "episode_delegations",
        (
            (
                row.delegation_id,
                row.topology_version,
                row.status.value,
                row.child_analysis_identity,
                row.child_episode_id,
                row_order,
                row.parent_session_id,
                row.parent_analysis_identity,
                row.parent_episode_id,
                row.rollup_owner_episode_id,
                row.spawn_tool_call_id,
                row.spawn_event_id,
                canonical_model_json(row),
            )
            for row_order, row in enumerate(analysis.delegations)
        ),
        13,
    )
    loaded = _load_persisted_analysis(connection, analysis.analysis_identity)
    if loaded != analysis:
        raise EpisodePersistenceConflict("persisted episode analysis differs")
    return loaded


def _insert_payload_rows(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    rows: Iterable[tuple[object, ...]],
    column_count: int,
) -> None:
    values = list(rows)
    if not values:
        return
    placeholders = ", ".join("?" for _ in range(column_count))
    connection.executemany(
        f"INSERT INTO {table_name} VALUES ({placeholders}) ON CONFLICT DO NOTHING",
        values,
    )


def _load_persisted_analysis(
    connection: duckdb.DuckDBPyConnection,
    analysis_identity: str,
) -> EpisodeAnalysis:
    run = connection.execute(
        """
        SELECT normalization_run_id, lifecycle_observation_id,
            segmentation_version, session_id, schema_version,
            episode_count, boundary_count, observation_count,
            membership_count, delegation_count
        FROM episode_analysis_runs WHERE analysis_identity = ?
        """,
        [analysis_identity],
    ).fetchone()
    if run is None:
        raise EpisodePersistenceConflict("persisted episode analysis is missing")

    def payloads(table: str, order_column: str) -> list[str]:
        identity_column = (
            "child_analysis_identity" if table == "episode_delegations" else "analysis_identity"
        )
        return [
            str(row[0])
            for row in connection.execute(
                f"SELECT payload_json FROM {table} "
                f"WHERE {identity_column} = ? "
                f"ORDER BY {order_column}",
                [analysis_identity],
            ).fetchall()
        ]

    episodes = [
        TaskEpisode.model_validate_json(value) for value in payloads("episodes", "episode_order")
    ]
    from session_doctor.schemas import EpisodeBoundary, EpisodeObservation

    boundaries = [
        EpisodeBoundary.model_validate_json(value)
        for value in payloads("episode_boundaries", "boundary_order")
    ]
    observations = [
        EpisodeObservation.model_validate_json(value)
        for value in payloads("episode_observations", "observation_order")
    ]
    memberships = [
        EpisodeEntityMembership.model_validate_json(value)
        for value in payloads("episode_entity_memberships", "membership_order")
    ]
    delegations = [
        EpisodeDelegation.model_validate_json(value)
        for value in payloads("episode_delegations", "delegation_order")
    ]
    actual_counts = (
        len(episodes),
        len(boundaries),
        len(observations),
        len(memberships),
        len(delegations),
    )
    if actual_counts != tuple(run[5:10]):
        raise EpisodePersistenceConflict("persisted episode analysis is incomplete")
    lifecycle_state_row = connection.execute(
        "SELECT state FROM lifecycle_observations WHERE lifecycle_observation_id = ?",
        [str(run[1])],
    ).fetchone()
    if lifecycle_state_row is None:
        raise EpisodePersistenceConflict("persisted lifecycle observation is missing")
    return EpisodeAnalysis(
        schema_version=str(run[4]),
        analysis_identity=analysis_identity,
        normalization_run_id=str(run[0]),
        segmentation_version=str(run[2]),
        session_id=str(run[3]),
        lifecycle_observation_id=str(run[1]),
        lifecycle_state=str(lifecycle_state_row[0]),
        episodes=episodes,
        boundaries=boundaries,
        observations=observations,
        entity_memberships=memberships,
        delegations=delegations,
    )
