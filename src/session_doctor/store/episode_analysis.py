from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import duckdb

from session_doctor.adapters import BaseAdapter
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    AnalysisAnchor,
    BoundaryDecision,
    BoundaryReason,
    EpisodeAnalysis,
    EpisodeAnalysisPayload,
    EpisodeBoundary,
    EpisodeDelegation,
    EpisodeExactInput,
    EpisodeMembership,
    EpisodeObservation,
    EpisodeTopologyCandidate,
    ParseWarning,
    SemanticAnalysisComponents,
    SemanticFoundation,
    Session,
    TaskEpisode,
)
from session_doctor.segmentation import SEGMENTATION_VERSION, analysis_anchors, segment_session
from session_doctor.semantic_foundations import semantic_analysis_identity

from .json_values import metadata_json, parse_metadata
from .lifecycle import LIFECYCLE_POLICY_VERSION, LifecycleObservation
from .normalization_runs import (
    NORMALIZATION_CONFIGURATION_HASH,
    NORMALIZATION_VERSION,
    StoredNormalization,
    load_normalization_from_connection,
    normalization_configuration_hash,
    parser_version_key,
    versions_compatible,
)

EPISODE_ANALYSIS_VERSION = "episode-analysis-v2"
EPISODE_ANCHOR_VERSION = "episode-anchor-v1"
EPISODE_MEMBERSHIP_VERSION = "episode-membership-v1"
TOPOLOGY_POLICY_VERSION = "delegation-topology-v1"


class EpisodePersistenceConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ExactEpisodeInput:
    analysis_identity: str
    stored: StoredNormalization
    lifecycle: LifecycleObservation
    foundation: SemanticFoundation
    snapshot_id: str
    snapshot_content_id: str
    logical_source_id: str

    @property
    def session(self) -> Session:
        session = self.stored.bundle.session
        if session is None:
            raise EpisodePersistenceConflict("normalization has no native session")
        return session


def select_exact_episode_input(
    connection: duckdb.DuckDBPyConnection,
    session_id: str,
    adapter: BaseAdapter,
    *,
    snapshot_id: str | None = None,
) -> ExactEpisodeInput:
    matches: dict[tuple[str, str, str, str], Session] = {}
    rows = connection.execute(
        """
        SELECT links.snapshot_bundle_id, snapshots.snapshot_id,
            snapshots.logical_source_id, snapshots.snapshot_content_id,
            entities.payload_json
        FROM normalized_entities AS entities
        JOIN normalization_run_bundles AS links USING (normalization_run_id)
        JOIN snapshot_bundles AS bundles USING (snapshot_bundle_id)
        JOIN source_snapshots AS snapshots
            ON snapshots.snapshot_id = bundles.primary_snapshot_id
        WHERE entities.entity_kind = 'session'
        """
    ).fetchall()
    for bundle_id, candidate_snapshot_id, logical_source_id, content_id, payload in rows:
        session = Session.model_validate_json(str(payload))
        if session.session_id != session_id:
            continue
        if snapshot_id is not None and str(candidate_snapshot_id) != snapshot_id:
            continue
        key = (
            str(bundle_id),
            str(candidate_snapshot_id),
            str(logical_source_id),
            str(content_id),
        )
        matches[key] = session
    if not matches:
        detail = (
            "requested snapshot has no normalized session"
            if snapshot_id
            else "session has no normalized v2 input"
        )
        raise ValueError(detail)
    source_identities = {(session.agent_name.value, key[2]) for key, session in matches.items()}
    if len(source_identities) != 1:
        raise EpisodePersistenceConflict("session resolves to multiple immutable logical sources")
    agent_name, logical_source_id = next(iter(source_identities))
    if agent_name != adapter.name.value:
        raise EpisodePersistenceConflict("adapter does not own immutable session input")

    if snapshot_id is None:
        latest = connection.execute(
            """
            SELECT snapshots.snapshot_id, bundles.snapshot_bundle_id,
                snapshots.snapshot_content_id
            FROM source_snapshots AS snapshots
            LEFT JOIN snapshot_bundles AS bundles
                ON bundles.primary_snapshot_id = snapshots.snapshot_id
            WHERE snapshots.logical_source_id = ?
            ORDER BY snapshots.capture_sequence DESC
            LIMIT 1
            """,
            [logical_source_id],
        ).fetchone()
        if latest is None or latest[1] is None:
            raise ValueError("latest capture has no current normalized projection")
        selected_snapshot_id = str(latest[0])
        bundle_id = str(latest[1])
        content_id = str(latest[2])
        if not any(key[0] == bundle_id and key[1] == selected_snapshot_id for key in matches):
            raise ValueError("latest capture has no current normalized projection")
    else:
        selected = {key for key in matches if key[1] == snapshot_id}
        bundle_ids = {key[0] for key in selected}
        if len(bundle_ids) != 1:
            raise EpisodePersistenceConflict("snapshot resolves to multiple primary bundles")
        key = min(selected)
        bundle_id, selected_snapshot_id, _, content_id = key

    selected_run_id = select_normalization_run(connection, bundle_id, adapter)
    stored = load_normalization_from_connection(connection, selected_run_id, bundle_id)
    if stored is None or stored.bundle.session is None:
        raise ValueError("normalization input is unavailable")
    if stored.bundle.session.session_id != session_id:
        raise EpisodePersistenceConflict("selected normalization session identity differs")
    lifecycle_row = connection.execute(
        """
        SELECT lifecycle_observation_id, snapshot_bundle_id,
            lifecycle_policy_version, state, observed_at, evidence_json
        FROM lifecycle_observations WHERE snapshot_bundle_id = ?
        """,
        [bundle_id],
    ).fetchone()
    if lifecycle_row is None:
        raise ValueError("lifecycle input is unavailable")
    if str(lifecycle_row[2]) != LIFECYCLE_POLICY_VERSION:
        raise EpisodePersistenceConflict("lifecycle policy version is incompatible")
    lifecycle = LifecycleObservation(
        lifecycle_observation_id=str(lifecycle_row[0]),
        snapshot_bundle_id=str(lifecycle_row[1]),
        state=str(lifecycle_row[3]),
        observed_at=lifecycle_row[4],
        evidence=parse_metadata(lifecycle_row[5]),
    )
    foundation_row = connection.execute(
        "SELECT foundation_json FROM normalization_semantics WHERE normalization_run_id = ?",
        [selected_run_id],
    ).fetchone()
    if foundation_row is None:
        raise ValueError("normalization semantics are unavailable")
    foundation = SemanticFoundation.model_validate_json(str(foundation_row[0]))
    components = episode_analysis_components(stored, lifecycle, foundation)
    return ExactEpisodeInput(
        analysis_identity=semantic_analysis_identity(components),
        stored=stored,
        lifecycle=lifecycle,
        foundation=foundation,
        snapshot_id=selected_snapshot_id,
        snapshot_content_id=content_id,
        logical_source_id=logical_source_id,
    )


def select_normalization_run(
    connection: duckdb.DuckDBPyConnection,
    snapshot_bundle_id: str,
    adapter: BaseAdapter,
) -> str:
    expected_configuration = normalization_configuration_hash(
        NORMALIZATION_CONFIGURATION_HASH,
        adapter.capabilities,
    )
    rows = connection.execute(
        """
        SELECT DISTINCT runs.normalization_run_id, runs.adapter_name,
            runs.adapter_version, runs.normalization_version,
            runs.configuration_hash
        FROM normalization_run_bundles AS links
        JOIN normalization_runs AS runs USING (normalization_run_id)
        WHERE links.snapshot_bundle_id = ?
        """,
        [snapshot_bundle_id],
    ).fetchall()
    eligible = [
        row
        for row in rows
        if str(row[1]) == adapter.name.value
        and str(row[3]) == NORMALIZATION_VERSION
        and str(row[4]) == expected_configuration
    ]
    exact = {str(row[0]) for row in eligible if str(row[2]) == adapter.version}
    if exact:
        if len(exact) != 1:
            raise EpisodePersistenceConflict("normalization version has competing identities")
        return next(iter(exact))
    current_key = parser_version_key(adapter.version)
    compatible = [
        row
        for row in eligible
        if versions_compatible(
            parser_version_key(str(row[2])),
            current_key,
            str(row[2]),
            adapter.version,
        )
    ]
    if not compatible:
        raise ValueError("normalization input is unavailable")
    winning_key = max(parser_version_key(str(row[2])) or (-1,) for row in compatible)
    winners = {
        str(row[0])
        for row in compatible
        if (parser_version_key(str(row[2])) or (-1,)) == winning_key
    }
    if len(winners) != 1:
        raise EpisodePersistenceConflict("normalization version has competing identities")
    return next(iter(winners))


def episode_analysis_components(
    stored: StoredNormalization,
    lifecycle: LifecycleObservation,
    foundation: SemanticFoundation,
) -> SemanticAnalysisComponents:
    configuration_hash = stable_id(
        EPISODE_ANALYSIS_VERSION,
        EPISODE_ANCHOR_VERSION,
        EPISODE_MEMBERSHIP_VERSION,
        TOPOLOGY_POLICY_VERSION,
    )
    return SemanticAnalysisComponents(
        normalization_run_id=stored.run.normalization_run_id,
        lifecycle_observation_id=lifecycle.lifecycle_observation_id,
        lifecycle_policy_version=LIFECYCLE_POLICY_VERSION,
        ordering_version=foundation.ordering.ordering_version,
        segmentation_version=SEGMENTATION_VERSION,
        relation_rule_set_version="unavailable-pr09",
        result_rule_set_version="unavailable-pr09",
        finding_rule_set_version="unavailable-pr09",
        facet_policy_version="unavailable-pr09",
        configuration_hash=configuration_hash,
    )


def persist_requested_episode_analysis(
    connection: duckdb.DuckDBPyConnection,
    exact: ExactEpisodeInput,
) -> EpisodeAnalysisPayload:
    components = episode_analysis_components(exact.stored, exact.lifecycle, exact.foundation)
    if semantic_analysis_identity(components) != exact.analysis_identity:
        raise EpisodePersistenceConflict("analysis identity is not canonical")
    persist_semantic_run(connection, exact.analysis_identity, components)
    analysis = segment_session(exact.stored.bundle, exact.lifecycle)
    persist_segmentation(connection, exact, analysis)
    candidate = root_topology_candidate(exact)
    role_tuple = (exact.analysis_identity, "requested")
    projection_id = stable_id(
        "episode-projection-v1",
        exact.analysis_identity,
        json.dumps([role_tuple], separators=(",", ":")),
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        components.configuration_hash,
    )
    persist_projection(connection, exact, projection_id, components.configuration_hash, candidate)
    memberships = derive_memberships(exact, analysis, projection_id)
    persist_memberships(connection, projection_id, memberships)
    return load_episode_analysis(connection, projection_id, exact.session.session_id)


def persist_semantic_run(
    connection: duckdb.DuckDBPyConnection,
    analysis_identity: str,
    components: SemanticAnalysisComponents,
) -> None:
    values = (
        analysis_identity,
        components.normalization_run_id,
        components.lifecycle_observation_id,
        components.lifecycle_policy_version,
        components.ordering_version,
        components.segmentation_version,
        components.relation_rule_set_version,
        components.result_rule_set_version,
        components.finding_rule_set_version,
        components.facet_policy_version,
        components.configuration_hash,
        None,
        None,
        metadata_json({"analysis_schema": EPISODE_ANALYSIS_VERSION}),
    )
    connection.execute(
        "INSERT INTO semantic_analysis_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        list(values),
    )
    stored = connection.execute(
        "SELECT * FROM semantic_analysis_runs WHERE analysis_identity = ?",
        [analysis_identity],
    ).fetchone()
    if stored is None or tuple(stored[:11]) != values[:11]:
        raise EpisodePersistenceConflict("analysis identity collision")


def persist_segmentation(
    connection: duckdb.DuckDBPyConnection,
    exact: ExactEpisodeInput,
    analysis: EpisodeAnalysis,
) -> None:
    identity = exact.analysis_identity
    anchors = analysis_anchors(exact.stored.bundle)
    episode_rows = [
        (
            identity,
            episode.episode_id,
            order,
            episode.segmentation_version,
            episode.session_id,
            episode.first_user_anchor_id,
            episode.last_user_anchor_id,
            episode.lifecycle_state,
            episode.provisional,
        )
        for order, episode in enumerate(analysis.episodes)
    ]
    user_anchor_rows = [
        (identity, episode.episode_id, order, *anchor_tuple(anchors[anchor_id]))
        for episode in analysis.episodes
        for order, anchor_id in enumerate(episode.user_anchor_ids)
    ]
    event_anchor_rows = [
        (identity, episode.episode_id, order, *anchor_tuple(anchors[anchor_id]))
        for episode in analysis.episodes
        for order, anchor_id in enumerate(episode.event_anchor_ids)
    ]
    boundary_rows = [
        (
            identity,
            boundary.boundary_id,
            order,
            boundary.left_user_anchor_id,
            boundary.right_user_anchor_id,
            boundary.decision.value,
            boundary.reason.value,
            boundary.broad_goal_similarity,
        )
        for order, boundary in enumerate(analysis.boundaries)
    ]
    boundary_evidence_rows = [
        (identity, boundary.boundary_id, order, *anchor_tuple(anchors[anchor_id]))
        for boundary in analysis.boundaries
        for order, anchor_id in enumerate(boundary.evidence_anchor_ids)
    ]
    episode_boundary_rows = [
        (identity, episode.episode_id, order, boundary_id)
        for episode in analysis.episodes
        for order, boundary_id in enumerate(episode.boundary_ids)
    ]
    observation_rows = [
        (
            identity,
            observation.observation_id,
            observation.episode_id,
            observation.observation_kind,
            order,
        )
        for order, observation in enumerate(analysis.observations)
    ]
    observation_evidence_rows = [
        (identity, observation.observation_id, order, *anchor_tuple(anchors[anchor_id]))
        for observation in analysis.observations
        for order, anchor_id in enumerate(observation.evidence_anchor_ids)
    ]
    tables = (
        ("episode_analysis_episodes", episode_rows),
        ("episode_analysis_user_anchors", user_anchor_rows),
        ("episode_analysis_event_anchors", event_anchor_rows),
        ("episode_analysis_boundaries", boundary_rows),
        ("episode_boundary_evidence", boundary_evidence_rows),
        ("episode_episode_boundaries", episode_boundary_rows),
        ("episode_analysis_observations", observation_rows),
        ("episode_observation_evidence", observation_evidence_rows),
    )
    for table, rows in tables:
        insert_and_compare(connection, table, rows, "analysis_identity = ?", [identity])


def anchor_tuple(anchor: AnalysisAnchor) -> tuple[object, ...]:
    return (
        anchor.anchor_id,
        anchor.anchor_kind,
        anchor.entity_id,
        anchor.payload_digest,
    )


def root_topology_candidate(exact: ExactEpisodeInput) -> EpisodeTopologyCandidate:
    source = exact.stored.source
    session = exact.session
    native_parent = session.parent_session_id or source.parent_source_id
    parent_status = source.metadata.get("claude_parent_link_status")
    is_native_child = native_parent is not None or parent_status not in {None, "missing"}
    status = "unavailable" if is_native_child else "not_child"
    reason = (
        "native_parent_counterpart_unresolved" if is_native_child else "no_native_child_evidence"
    )
    candidate_id = stable_id(
        "topology-candidate",
        TOPOLOGY_POLICY_VERSION,
        "parent",
        source.source_id,
        exact.logical_source_id,
        exact.snapshot_content_id,
        native_parent or "missing",
        status,
    )
    return EpisodeTopologyCandidate(
        topology_candidate_id=candidate_id,
        direction="parent",
        native_spawn_identity=None,
        child_analysis_identity=exact.analysis_identity,
        status=status,
        reason=reason,
        endpoint_status="unavailable" if is_native_child else "missing",
        witness_bundle_ids=[exact.stored.run.snapshot_bundle_id],
    )


def persist_projection(
    connection: duckdb.DuckDBPyConnection,
    exact: ExactEpisodeInput,
    projection_id: str,
    configuration_hash: str,
    candidate: EpisodeTopologyCandidate,
) -> None:
    projection_values = (
        projection_id,
        exact.analysis_identity,
        exact.session.session_id,
        TOPOLOGY_POLICY_VERSION,
        configuration_hash,
    )
    connection.execute(
        """
        INSERT INTO episode_projection_runs (
            episode_projection_id, requested_analysis_identity,
            requested_session_id, topology_policy_version, configuration_hash
        ) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
        """,
        list(projection_values),
    )
    stored = connection.execute(
        "SELECT * EXCLUDE (created_at) FROM episode_projection_runs "
        "WHERE episode_projection_id = ?",
        [projection_id],
    ).fetchone()
    if stored != projection_values:
        raise EpisodePersistenceConflict("episode projection identity collision")
    input_rows = [
        (
            projection_id,
            exact.analysis_identity,
            0,
            "requested",
            exact.session.session_id,
            exact.stored.run.normalization_run_id,
            exact.stored.run.snapshot_bundle_id,
            exact.lifecycle.lifecycle_observation_id,
        )
    ]
    insert_and_compare(
        connection,
        "episode_projection_inputs",
        input_rows,
        "episode_projection_id = ?",
        [projection_id],
    )
    candidate_row = (
        projection_id,
        candidate.topology_candidate_id,
        candidate.direction,
        candidate.native_spawn_identity,
        None,
        None,
        None,
        exact.stored.source.source_id,
        exact.logical_source_id,
        exact.snapshot_content_id,
        candidate.parent_analysis_identity,
        candidate.child_analysis_identity,
        candidate.status,
        candidate.reason,
        candidate.endpoint_status,
    )
    insert_and_compare(
        connection,
        "episode_topology_candidates",
        [candidate_row],
        "episode_projection_id = ?",
        [projection_id],
    )
    witness_rows = [
        (
            projection_id,
            candidate.topology_candidate_id,
            witness_id,
            None,
            exact.snapshot_id,
            None,
            None,
            None,
        )
        for witness_id in candidate.witness_bundle_ids
    ]
    insert_and_compare(
        connection,
        "episode_topology_candidate_witnesses",
        witness_rows,
        "episode_projection_id = ?",
        [projection_id],
    )


def derive_memberships(
    exact: ExactEpisodeInput,
    analysis: EpisodeAnalysis,
    _projection_id: str,
) -> list[EpisodeMembership]:
    bundle = exact.stored.bundle
    anchors = analysis_anchors(bundle)
    episode_by_anchor: dict[str, set[str]] = {}
    for episode in analysis.episodes:
        for anchor_id in episode.event_anchor_ids:
            episode_by_anchor.setdefault(anchor_id, set()).add(episode.episode_id)
    raw_by_id = {event.event_id: event for event in bundle.raw_events}
    message_by_id = {message.message_id: message for message in bundle.messages}
    call_by_id = {call.tool_call_id: call for call in bundle.tool_calls}
    entity_objects = {
        "session_source": {exact.stored.source.source_id: exact.stored.source},
        "session": (
            {exact.session.session_id: exact.session} if exact.stored.bundle.session else {}
        ),
        "raw_event": raw_by_id,
        "message": message_by_id,
        "tool_call": call_by_id,
        "tool_result": {row.tool_result_id: row for row in bundle.tool_results},
        "command_run": {row.command_run_id: row for row in bundle.command_runs},
        "file_activity": {row.file_activity_id: row for row in bundle.file_activities},
        "model_usage": {row.model_usage_id: row for row in bundle.model_usage},
        "parse_warning": {row.warning_id: row for row in bundle.parse_warnings},
    }
    rows = exact_input_entity_rows(exact)
    memberships: list[EpisodeMembership] = []
    for entity_kind, entity_id, entity_order, _payload in rows:
        entity = entity_objects[entity_kind][entity_id]
        candidates: set[str] = set()
        reason = "no_deterministic_anchor"
        if entity_kind in {"session", "session_source"}:
            reason = "container_not_episode_evidence"
        elif entity_kind == "raw_event":
            anchor = next(
                (
                    anchor
                    for anchor in anchors.values()
                    if anchor.entity_id == entity_id and anchor.anchor_kind == "raw_event"
                ),
                None,
            )
            if anchor is not None:
                candidates.update(episode_by_anchor.get(anchor.anchor_id, set()))
        elif entity_kind == "parse_warning":
            warning = entity
            if isinstance(warning, ParseWarning) and warning.record_index is not None:
                event_matches = [
                    event
                    for event in bundle.raw_events
                    if event.source_id == warning.source_id
                    and event.record_index == warning.record_index
                ]
                for event in event_matches:
                    anchor = next(
                        row for row in anchors.values() if row.entity_id == event.event_id
                    )
                    candidates.update(episode_by_anchor.get(anchor.anchor_id, set()))
        else:
            source_event_id = getattr(entity, "source_event_id", None)
            if source_event_id in raw_by_id:
                event = raw_by_id[source_event_id]
                anchor = next(row for row in anchors.values() if row.entity_id == event.event_id)
                candidates.update(episode_by_anchor.get(anchor.anchor_id, set()))
            if not candidates and entity_kind in {"tool_result", "command_run", "file_activity"}:
                tool_call_id = getattr(entity, "tool_call_id", None)
                call = call_by_id.get(tool_call_id or "")
                if call is not None and call.source_event_id in raw_by_id:
                    anchor = next(
                        row
                        for row in anchors.values()
                        if row.entity_id == raw_by_id[call.source_event_id].event_id
                    )
                    candidates.update(episode_by_anchor.get(anchor.anchor_id, set()))
            if not candidates and entity_kind == "message":
                parent = message_by_id.get(getattr(entity, "parent_message_id", None) or "")
                if parent is not None:
                    parent_anchor = next(
                        (
                            row
                            for row in anchors.values()
                            if row.entity_id in {parent.source_event_id, parent.message_id}
                        ),
                        None,
                    )
                    if parent_anchor is not None:
                        candidates.update(episode_by_anchor.get(parent_anchor.anchor_id, set()))
        ordered_candidates = sorted(
            (exact.analysis_identity, episode_id) for episode_id in candidates
        )
        if len(candidates) == 1:
            episode_id = next(iter(candidates))
            memberships.append(
                EpisodeMembership(
                    source_analysis_identity=exact.analysis_identity,
                    entity_kind=entity_kind,
                    entity_id=entity_id,
                    normalization_run_id=exact.stored.run.normalization_run_id,
                    entity_order=entity_order,
                    membership_status="assigned",
                    source_episode_id=episode_id,
                    rollup_owner_status="known",
                    rollup_owner_analysis_identity=exact.analysis_identity,
                    rollup_owner_episode_id=episode_id,
                    aggregate_eligibility="direct",
                    reason="deterministic_episode_anchor",
                )
            )
        elif candidates:
            memberships.append(
                EpisodeMembership(
                    source_analysis_identity=exact.analysis_identity,
                    entity_kind=entity_kind,
                    entity_id=entity_id,
                    normalization_run_id=exact.stored.run.normalization_run_id,
                    entity_order=entity_order,
                    membership_status="ambiguous",
                    rollup_owner_status="unavailable",
                    aggregate_eligibility="ineligible",
                    reason="multiple_episode_candidates",
                    candidate_episode_keys=ordered_candidates,
                )
            )
        else:
            if reason == "no_deterministic_anchor" and analysis.episodes:
                reason = "before_first_episode"
            elif reason == "no_deterministic_anchor" and not analysis.episodes:
                reason = "no_episode_anchor"
            memberships.append(
                EpisodeMembership(
                    source_analysis_identity=exact.analysis_identity,
                    entity_kind=entity_kind,
                    entity_id=entity_id,
                    normalization_run_id=exact.stored.run.normalization_run_id,
                    entity_order=entity_order,
                    membership_status="unassigned",
                    rollup_owner_status="unavailable",
                    aggregate_eligibility="ineligible",
                    reason=reason,
                )
            )
    return memberships


def exact_input_entity_rows(exact: ExactEpisodeInput) -> list[tuple[str, str, int, str]]:
    # Callers select this inside the same transaction; the run is immutable.
    from .normalization_runs import normalized_entity_rows

    return list(normalized_entity_rows(exact.stored.source, exact.stored.bundle))


def persist_memberships(
    connection: duckdb.DuckDBPyConnection,
    projection_id: str,
    memberships: list[EpisodeMembership],
) -> None:
    rows = [
        (
            projection_id,
            row.source_analysis_identity,
            row.entity_kind,
            row.entity_id,
            row.normalization_run_id,
            row.entity_order,
            row.membership_status,
            row.source_episode_id,
            row.rollup_owner_status,
            row.rollup_owner_analysis_identity,
            row.rollup_owner_episode_id,
            row.aggregate_eligibility,
            row.reason,
            json.dumps(row.candidate_episode_keys, separators=(",", ":")),
        )
        for row in memberships
    ]
    insert_and_compare(
        connection,
        "episode_entity_memberships",
        rows,
        "episode_projection_id = ?",
        [projection_id],
    )


def insert_and_compare(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    rows: Sequence[tuple[object, ...]],
    where: str,
    params: list[object],
) -> None:
    if rows:
        placeholders = ", ".join("?" for _ in rows[0])
        connection.executemany(
            f"INSERT INTO {table} VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            rows,
        )
    stored = connection.execute(
        f"SELECT * FROM {table} WHERE {where} ORDER BY ALL", params
    ).fetchall()
    if sorted(stored) != sorted(rows):
        raise EpisodePersistenceConflict(f"{table} replay differs for semantic identity")


def load_episode_analysis(
    connection: duckdb.DuckDBPyConnection,
    projection_id: str,
    requested_session_id: str,
) -> EpisodeAnalysisPayload:
    projection = connection.execute(
        """
        SELECT requested_analysis_identity, requested_session_id
        FROM episode_projection_runs WHERE episode_projection_id = ?
        """,
        [projection_id],
    ).fetchone()
    if projection is None:
        raise ValueError("episode projection is unavailable")
    if str(projection[1]) != requested_session_id:
        raise EpisodePersistenceConflict("projection belongs to a different requested session")
    identity = str(projection[0])
    input_rows = connection.execute(
        """
        SELECT analysis_identity, discovery_role, session_id,
            normalization_run_id, snapshot_bundle_id, lifecycle_observation_id
        FROM episode_projection_inputs WHERE episode_projection_id = ?
        ORDER BY input_order, analysis_identity
        """,
        [projection_id],
    ).fetchall()
    exact_inputs = [
        EpisodeExactInput(
            analysis_identity=str(row[0]),
            discovery_role=cast(Any, str(row[1])),
            session_id=str(row[2]),
            normalization_run_id=str(row[3]),
            snapshot_bundle_id=str(row[4]),
            lifecycle_observation_id=str(row[5]),
        )
        for row in input_rows
    ]
    episodes = load_episodes(connection, identity)
    boundaries = load_boundaries(connection, identity, requested_session_id)
    observations = load_observations(connection, identity)
    membership_rows = connection.execute(
        """
        SELECT source_analysis_identity, entity_kind, entity_id,
            normalization_run_id, entity_order, membership_status,
            source_episode_id, rollup_owner_status,
            rollup_owner_analysis_identity, rollup_owner_episode_id,
            aggregate_eligibility, reason, candidate_episode_keys_json
        FROM episode_entity_memberships
        WHERE episode_projection_id = ? AND source_analysis_identity = ?
        ORDER BY entity_kind, entity_order, entity_id
        """,
        [projection_id, identity],
    ).fetchall()
    memberships = [
        EpisodeMembership(
            source_analysis_identity=str(row[0]),
            entity_kind=str(row[1]),
            entity_id=str(row[2]),
            normalization_run_id=str(row[3]),
            entity_order=int(row[4]),
            membership_status=cast(Any, str(row[5])),
            source_episode_id=str(row[6]) if row[6] is not None else None,
            rollup_owner_status=cast(Any, str(row[7])),
            rollup_owner_analysis_identity=str(row[8]) if row[8] is not None else None,
            rollup_owner_episode_id=str(row[9]) if row[9] is not None else None,
            aggregate_eligibility=cast(Any, str(row[10])),
            reason=str(row[11]),
            candidate_episode_keys=[tuple(item) for item in json.loads(str(row[12]))],
        )
        for row in membership_rows
    ]
    candidate_rows = connection.execute(
        """
        SELECT c.topology_candidate_id, c.direction, c.native_spawn_identity,
            c.parent_analysis_identity, c.child_analysis_identity, c.status,
            c.reason, c.endpoint_status,
            coalesce(list(w.witness_bundle_id ORDER BY w.witness_bundle_id)
                FILTER (WHERE w.witness_bundle_id IS NOT NULL), [])
        FROM episode_topology_candidates AS c
        LEFT JOIN episode_topology_candidate_witnesses AS w
            USING (episode_projection_id, topology_candidate_id)
        WHERE c.episode_projection_id = ?
        GROUP BY ALL ORDER BY c.topology_candidate_id
        """,
        [projection_id],
    ).fetchall()
    candidates = [
        EpisodeTopologyCandidate(
            topology_candidate_id=str(row[0]),
            direction=cast(Any, str(row[1])),
            native_spawn_identity=str(row[2]) if row[2] is not None else None,
            parent_analysis_identity=str(row[3]) if row[3] is not None else None,
            child_analysis_identity=str(row[4]) if row[4] is not None else None,
            status=cast(Any, str(row[5])),
            reason=str(row[6]),
            endpoint_status=cast(Any, str(row[7])),
            witness_bundle_ids=[str(item) for item in row[8]],
        )
        for row in candidate_rows
    ]
    return EpisodeAnalysisPayload(
        requested_session_id=requested_session_id,
        analysis_identity=identity,
        episode_projection_id=projection_id,
        exact_inputs=exact_inputs,
        episodes=episodes,
        boundaries=boundaries,
        observations=observations,
        memberships=memberships,
        delegation=EpisodeDelegation(candidates=candidates),
    )


def load_episodes(
    connection: duckdb.DuckDBPyConnection, analysis_identity: str
) -> list[TaskEpisode]:
    rows = connection.execute(
        """
        SELECT episode_id, segmentation_version, session_id,
            first_user_analysis_anchor_id, last_user_analysis_anchor_id,
            lifecycle_state, provisional
        FROM episode_analysis_episodes WHERE analysis_identity = ?
        ORDER BY episode_order, episode_id
        """,
        [analysis_identity],
    ).fetchall()
    episodes: list[TaskEpisode] = []
    for row in rows:
        episode_id = str(row[0])
        user_ids = connection.execute(
            """
            SELECT anchor_id FROM episode_analysis_user_anchors
            WHERE analysis_identity = ? AND episode_id = ? ORDER BY anchor_order
            """,
            [analysis_identity, episode_id],
        ).fetchall()
        event_ids = connection.execute(
            """
            SELECT anchor_id FROM episode_analysis_event_anchors
            WHERE analysis_identity = ? AND episode_id = ? ORDER BY anchor_order
            """,
            [analysis_identity, episode_id],
        ).fetchall()
        boundary_ids = connection.execute(
            """
            SELECT boundary_id FROM episode_episode_boundaries
            WHERE analysis_identity = ? AND episode_id = ? ORDER BY boundary_order
            """,
            [analysis_identity, episode_id],
        ).fetchall()
        episodes.append(
            TaskEpisode(
                episode_id=episode_id,
                segmentation_version=str(row[1]),
                session_id=str(row[2]),
                first_user_anchor_id=str(row[3]),
                last_user_anchor_id=str(row[4]),
                user_anchor_ids=[str(item[0]) for item in user_ids],
                event_anchor_ids=[str(item[0]) for item in event_ids],
                boundary_ids=[str(item[0]) for item in boundary_ids],
                lifecycle_state=str(row[5]),
                provisional=bool(row[6]),
            )
        )
    return episodes


def load_boundaries(
    connection: duckdb.DuckDBPyConnection,
    analysis_identity: str,
    session_id: str,
) -> list[EpisodeBoundary]:
    rows = connection.execute(
        """
        SELECT boundary_id, left_user_analysis_anchor_id,
            right_user_analysis_anchor_id, decision, reason, broad_goal_similarity
        FROM episode_analysis_boundaries WHERE analysis_identity = ?
        ORDER BY boundary_order, boundary_id
        """,
        [analysis_identity],
    ).fetchall()
    boundaries: list[EpisodeBoundary] = []
    for row in rows:
        evidence = connection.execute(
            """
            SELECT evidence_anchor_id FROM episode_boundary_evidence
            WHERE analysis_identity = ? AND boundary_id = ? ORDER BY evidence_order
            """,
            [analysis_identity, row[0]],
        ).fetchall()
        boundaries.append(
            EpisodeBoundary(
                boundary_id=str(row[0]),
                segmentation_version=SEGMENTATION_VERSION,
                session_id=session_id,
                left_user_anchor_id=str(row[1]),
                right_user_anchor_id=str(row[2]),
                decision=BoundaryDecision(str(row[3])),
                reason=BoundaryReason(str(row[4])),
                evidence_anchor_ids=[str(item[0]) for item in evidence],
                broad_goal_similarity=float(row[5]) if row[5] is not None else None,
            )
        )
    return boundaries


def load_observations(
    connection: duckdb.DuckDBPyConnection, analysis_identity: str
) -> list[EpisodeObservation]:
    rows = connection.execute(
        """
        SELECT observation_id, episode_id, observation_kind
        FROM episode_analysis_observations WHERE analysis_identity = ?
        ORDER BY observation_order, observation_id
        """,
        [analysis_identity],
    ).fetchall()
    observations: list[EpisodeObservation] = []
    for row in rows:
        evidence = connection.execute(
            """
            SELECT evidence_anchor_id FROM episode_observation_evidence
            WHERE analysis_identity = ? AND observation_id = ? ORDER BY evidence_order
            """,
            [analysis_identity, row[0]],
        ).fetchall()
        observations.append(
            EpisodeObservation(
                observation_id=str(row[0]),
                episode_id=str(row[1]),
                observation_kind=str(row[2]),
                evidence_anchor_ids=[str(item[0]) for item in evidence],
            )
        )
    return observations
