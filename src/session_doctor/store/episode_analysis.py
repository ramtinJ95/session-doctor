from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import duckdb

from session_doctor.adapters import BaseAdapter, built_in_adapters
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    AnalysisAnchor,
    BoundaryDecision,
    BoundaryReason,
    EpisodeAnalysis,
    EpisodeAnalysisPayload,
    EpisodeBoundary,
    EpisodeDelegation,
    EpisodeDelegationBinding,
    EpisodeDelegationEdge,
    EpisodeExactInput,
    EpisodeMembership,
    EpisodeObservation,
    EpisodeTopologyCandidate,
    ParseWarning,
    RawEvent,
    SemanticAnalysisComponents,
    SemanticFoundation,
    Session,
    SessionSource,
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


@dataclass(frozen=True)
class TopologyWitness:
    bundle_id: str
    parent_snapshot_id: str
    child_snapshot_id: str


@dataclass(frozen=True)
class TopologyCandidateState:
    candidate: EpisodeTopologyCandidate
    parent: ExactEpisodeInput | None
    child: ExactEpisodeInput | None
    witnesses: tuple[TopologyWitness, ...]
    child_source: SessionSource | None = None
    spawn_entity_id: str | None = None
    spawn_anchor_id: str | None = None
    parent_episode_id: str | None = None


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
    adapter = next(
        item for item in built_in_adapters() if item.name.value == exact.stored.run.adapter_name
    )
    inputs, roles, unresolved = discover_topology_inputs(connection, exact, adapter)
    analyses: dict[str, EpisodeAnalysis] = {}
    for item in inputs.values():
        components = episode_analysis_components(item.stored, item.lifecycle, item.foundation)
        if semantic_analysis_identity(components) != item.analysis_identity:
            raise EpisodePersistenceConflict("analysis identity is not canonical")
        persist_semantic_run(connection, item.analysis_identity, components)
        analysis = segment_session(item.stored.bundle, item.lifecycle)
        analyses[item.analysis_identity] = analysis
        persist_segmentation(connection, item, analysis)
    candidates = derive_topology_candidates(
        connection,
        inputs,
        roles,
        analyses,
        exact,
        unresolved,
    )
    ordered_inputs = sorted(
        inputs.values(),
        key=lambda item: (
            {"requested": 0, "ancestor": 1, "descendant": 2, "candidate": 3}[
                roles[item.analysis_identity]
            ],
            item.session.session_id,
            item.analysis_identity,
        ),
    )
    role_tuples = [
        (item.analysis_identity, roles[item.analysis_identity]) for item in ordered_inputs
    ]
    candidate_identity = [
        {
            **state.candidate.model_dump(mode="json"),
            "witnesses": [witness.bundle_id for witness in state.witnesses],
        }
        for state in sorted(candidates, key=lambda row: row.candidate.topology_candidate_id)
    ]
    components = episode_analysis_components(exact.stored, exact.lifecycle, exact.foundation)
    projection_id = stable_id(
        "episode-projection-v1",
        exact.analysis_identity,
        json.dumps(role_tuples, separators=(",", ":")),
        json.dumps(candidate_identity, sort_keys=True, separators=(",", ":")),
        components.configuration_hash,
    )
    persist_topology_projection(
        connection,
        exact,
        projection_id,
        components.configuration_hash,
        ordered_inputs,
        roles,
        candidates,
        analyses,
    )
    return load_episode_analysis(connection, projection_id, exact.session.session_id)


def discover_topology_inputs(
    connection: duckdb.DuckDBPyConnection,
    requested: ExactEpisodeInput,
    adapter: BaseAdapter,
) -> tuple[
    dict[str, ExactEpisodeInput],
    dict[str, str],
    list[tuple[ExactEpisodeInput, SessionSource, str]],
]:
    inputs = {requested.analysis_identity: requested}
    roles = {requested.analysis_identity: "requested"}
    by_source = immutable_sessions_by_source(connection)
    source_models = immutable_source_models(connection)
    session_models = immutable_session_models(connection)
    unresolved: list[tuple[ExactEpisodeInput, SessionSource, str]] = []
    queue = [requested]
    while queue:
        current = queue.pop(0)
        source = current.stored.source
        related: list[tuple[str, str, str]] = []
        if source.parent_source_id:
            related.extend(
                (session_id, "ancestor", source.parent_source_id)
                for session_id in by_source.get(source.parent_source_id, set())
            )
        if current.session.parent_session_id:
            parent_endpoint = session_models.get(current.session.parent_session_id)
            if parent_endpoint is not None:
                related.append((current.session.parent_session_id, "ancestor", parent_endpoint[1]))
        related.extend(
            (session_id, "descendant", child_source.source_id)
            for child_source in source_models.values()
            if child_source.parent_source_id == source.source_id
            for session_id in by_source.get(child_source.source_id, set())
        )
        related.extend(
            (session_id, "descendant", source_id)
            for session_id, (session, source_id) in session_models.items()
            if session.parent_session_id == current.session.session_id
        )
        for session_id, proposed_role, related_source_id in sorted(set(related)):
            try:
                candidate = select_exact_episode_input(connection, session_id, adapter)
            except (ValueError, EpisodePersistenceConflict) as exc:
                source_model = source_models.get(related_source_id)
                if source_model is not None:
                    unresolved.append((current, source_model, str(exc)))
                continue
            existing = inputs.get(candidate.analysis_identity)
            if existing is None:
                inputs[candidate.analysis_identity] = candidate
                roles[candidate.analysis_identity] = proposed_role
                queue.append(candidate)
            elif roles[candidate.analysis_identity] != proposed_role and candidate is not requested:
                roles[candidate.analysis_identity] = "candidate"
    return inputs, roles, unresolved


def immutable_sessions_by_source(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, set[str]]:
    rows = connection.execute(
        """
        SELECT normalization_run_id, entity_kind, payload_json
        FROM normalized_entities
        WHERE entity_kind IN ('session_source', 'session')
        ORDER BY normalization_run_id, entity_kind
        """
    ).fetchall()
    by_run: dict[str, dict[str, list[str]]] = {}
    for run_id, kind, payload in rows:
        by_run.setdefault(str(run_id), {}).setdefault(str(kind), []).append(str(payload))
    result: dict[str, set[str]] = {}
    for entities in by_run.values():
        sources = entities.get("session_source", [])
        sessions = entities.get("session", [])
        if len(sources) != 1 or len(sessions) != 1:
            continue
        source_id = json.loads(sources[0]).get("source_id")
        session_id = json.loads(sessions[0]).get("session_id")
        if isinstance(source_id, str) and isinstance(session_id, str):
            result.setdefault(source_id, set()).add(session_id)
    return result


def immutable_source_models(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, SessionSource]:
    rows = connection.execute(
        "SELECT payload_json FROM normalized_entities "
        "WHERE entity_kind = 'session_source' ORDER BY payload_json"
    ).fetchall()
    result: dict[str, SessionSource] = {}
    for (payload,) in rows:
        source = SessionSource.model_validate_json(str(payload))
        existing = result.get(source.source_id)
        if existing is None or (
            (source.parent_source_id is not None, len(source.metadata))
            > (existing.parent_source_id is not None, len(existing.metadata))
        ):
            result[source.source_id] = source
    return result


def immutable_session_models(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[Session, str]]:
    rows = connection.execute(
        """
        SELECT sessions.payload_json, sources.payload_json
        FROM normalized_entities AS sessions
        JOIN normalized_entities AS sources USING (normalization_run_id)
        WHERE sessions.entity_kind = 'session'
            AND sources.entity_kind = 'session_source'
        ORDER BY sessions.payload_json, sources.payload_json
        """
    ).fetchall()
    result: dict[str, tuple[Session, str]] = {}
    for session_payload, source_payload in rows:
        session = Session.model_validate_json(str(session_payload))
        source = SessionSource.model_validate_json(str(source_payload))
        existing = result.get(session.session_id)
        if existing is None or (
            session.parent_session_id is not None,
            len(session.metadata),
        ) > (
            existing[0].parent_session_id is not None,
            len(existing[0].metadata),
        ):
            result[session.session_id] = (session, source.source_id)
    return result


def derive_topology_candidates(
    connection: duckdb.DuckDBPyConnection,
    inputs: dict[str, ExactEpisodeInput],
    roles: dict[str, str],
    analyses: dict[str, EpisodeAnalysis],
    requested: ExactEpisodeInput,
    unresolved: list[tuple[ExactEpisodeInput, SessionSource, str]],
) -> list[TopologyCandidateState]:
    by_source = {item.stored.source.source_id: item for item in inputs.values()}
    states: list[TopologyCandidateState] = []
    parent_by_child: dict[str, str] = {}
    for child in sorted(inputs.values(), key=lambda item: item.analysis_identity):
        source = child.stored.source
        parent = by_source.get(source.parent_source_id or "")
        sidecar = source.metadata.get("claude_subagent_metadata")
        spawn_identity = sidecar.get("tool_use_id") if isinstance(sidecar, dict) else None
        if not isinstance(spawn_identity, str):
            spawn_identity = None
        native_child = (
            source.parent_source_id is not None or child.session.parent_session_id is not None
        )
        if parent is None and not native_child:
            if child is requested:
                candidate = root_topology_candidate(child)
                states.append(
                    TopologyCandidateState(
                        candidate=candidate,
                        parent=None,
                        child=child,
                        witnesses=(),
                    )
                )
            continue
        witnesses = topology_witnesses(connection, parent, child) if parent else ()
        status = "unavailable"
        reason = "parent_exact_input_unavailable"
        spawn_entity_id = None
        spawn_anchor_id = None
        parent_episode_id = None
        if parent is not None:
            parent_by_child[child.analysis_identity] = parent.analysis_identity
            calls = [
                call
                for call in parent.stored.bundle.tool_calls
                if call.native_tool_call_id == spawn_identity
            ]
            if spawn_identity is None:
                reason = "native_spawn_identity_unavailable"
            elif not witnesses:
                reason = "complete_topology_witness_unavailable"
            elif len(calls) != 1:
                reason = "native_spawn_not_unique"
                status = "ambiguous" if len(calls) > 1 else "unavailable"
            elif calls[0].source_event_id is None:
                reason = "spawn_event_unavailable"
            else:
                anchors = analysis_anchors(parent.stored.bundle)
                spawn_anchor = next(
                    (
                        anchor
                        for anchor in anchors.values()
                        if anchor.anchor_kind == "raw_event"
                        and anchor.entity_id == calls[0].source_event_id
                    ),
                    None,
                )
                parent_episodes = (
                    [
                        episode.episode_id
                        for episode in analyses[parent.analysis_identity].episodes
                        if spawn_anchor is not None
                        and spawn_anchor.anchor_id in episode.event_anchor_ids
                    ]
                    if spawn_anchor is not None
                    else []
                )
                if len(parent_episodes) != 1:
                    reason = "spawn_parent_episode_unavailable"
                    status = "ambiguous" if len(parent_episodes) > 1 else "unavailable"
                else:
                    assert spawn_anchor is not None
                    status = "linked"
                    reason = "native_spawn_exact_handshake"
                    spawn_entity_id = calls[0].tool_call_id
                    spawn_anchor_id = spawn_anchor.anchor_id
                    parent_episode_id = parent_episodes[0]
        witness_ids = [witness.bundle_id for witness in witnesses]
        candidate_id = stable_id(
            "topology-candidate",
            TOPOLOGY_POLICY_VERSION,
            "parent",
            parent.stored.source.source_id if parent else "missing",
            parent.logical_source_id if parent else "missing",
            parent.snapshot_content_id if parent else "unavailable",
            child.stored.source.source_id,
            child.logical_source_id,
            child.snapshot_content_id,
            spawn_identity or "missing",
            status,
            json.dumps(witness_ids, separators=(",", ":")),
        )
        candidate = EpisodeTopologyCandidate(
            topology_candidate_id=candidate_id,
            direction="parent",
            native_spawn_identity=spawn_identity,
            parent_analysis_identity=parent.analysis_identity if parent else None,
            child_analysis_identity=child.analysis_identity,
            status=status,
            reason=reason,
            endpoint_status="observed" if parent else "unavailable",
            witness_bundle_ids=witness_ids,
        )
        states.append(
            TopologyCandidateState(
                candidate=candidate,
                parent=parent,
                child=child,
                witnesses=witnesses,
                spawn_entity_id=spawn_entity_id,
                spawn_anchor_id=spawn_anchor_id,
                parent_episode_id=parent_episode_id,
            )
        )
    cyclic = cyclic_analysis_ids(parent_by_child)
    if cyclic:
        roles.update(
            {
                identity: "candidate"
                for identity in cyclic
                if identity != requested.analysis_identity
            }
        )
        states = [
            TopologyCandidateState(
                candidate=state.candidate.model_copy(
                    update={"status": "ambiguous", "reason": "delegation_cycle"}
                ),
                parent=state.parent,
                child=state.child,
                witnesses=state.witnesses,
            )
            if state.child is not None and state.child.analysis_identity in cyclic
            else state
            for state in states
        ]
    for observed, missing_source, detail in sorted(
        unresolved, key=lambda row: (row[0].analysis_identity, row[1].source_id)
    ):
        missing_is_parent = observed.stored.source.parent_source_id == missing_source.source_id
        candidate_id = stable_id(
            "topology-candidate",
            TOPOLOGY_POLICY_VERSION,
            "parent" if missing_is_parent else "child",
            missing_source.source_id,
            observed.analysis_identity,
            detail,
        )
        states.append(
            TopologyCandidateState(
                candidate=EpisodeTopologyCandidate(
                    topology_candidate_id=candidate_id,
                    direction="parent" if missing_is_parent else "child",
                    parent_analysis_identity=(
                        None if missing_is_parent else observed.analysis_identity
                    ),
                    child_analysis_identity=(
                        observed.analysis_identity if missing_is_parent else None
                    ),
                    status="unavailable",
                    reason="counterpart_exact_input_unavailable",
                    endpoint_status="unavailable",
                ),
                parent=None if missing_is_parent else observed,
                child=observed if missing_is_parent else None,
                child_source=observed.stored.source if missing_is_parent else missing_source,
                witnesses=(),
            )
        )
    return states


def topology_witnesses(
    connection: duckdb.DuckDBPyConnection,
    parent: ExactEpisodeInput,
    child: ExactEpisodeInput,
) -> tuple[TopologyWitness, ...]:
    rows = connection.execute(
        """
        SELECT members.snapshot_bundle_id,
            max(CASE WHEN snapshots.source_id = ? AND snapshots.snapshot_content_id = ?
                THEN snapshots.snapshot_id END) AS parent_snapshot_id,
            max(CASE WHEN snapshots.source_id = ? AND snapshots.snapshot_content_id = ?
                THEN snapshots.snapshot_id END) AS child_snapshot_id
        FROM snapshot_bundle_members AS members
        JOIN source_snapshots AS snapshots USING (snapshot_id, logical_source_id)
        JOIN bundle_capture_metadata AS capture USING (snapshot_bundle_id)
        WHERE capture.capture_status = 'complete'
        GROUP BY members.snapshot_bundle_id
        HAVING parent_snapshot_id IS NOT NULL AND child_snapshot_id IS NOT NULL
        ORDER BY members.snapshot_bundle_id
        """,
        [
            parent.stored.source.source_id,
            parent.snapshot_content_id,
            child.stored.source.source_id,
            child.snapshot_content_id,
        ],
    ).fetchall()
    return tuple(
        TopologyWitness(
            bundle_id=str(row[0]),
            parent_snapshot_id=str(row[1]),
            child_snapshot_id=str(row[2]),
        )
        for row in rows
    )


def cyclic_analysis_ids(parent_by_child: dict[str, str]) -> set[str]:
    cyclic: set[str] = set()
    for start in parent_by_child:
        path: list[str] = []
        current = start
        while current in parent_by_child and current not in path:
            path.append(current)
            current = parent_by_child[current]
        if current in path:
            cyclic.update(path[path.index(current) :])
    return cyclic


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


def persist_topology_projection(
    connection: duckdb.DuckDBPyConnection,
    requested: ExactEpisodeInput,
    projection_id: str,
    configuration_hash: str,
    ordered_inputs: list[ExactEpisodeInput],
    roles: dict[str, str],
    candidates: list[TopologyCandidateState],
    analyses: dict[str, EpisodeAnalysis],
) -> None:
    projection_values = (
        projection_id,
        requested.analysis_identity,
        requested.session.session_id,
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
            item.analysis_identity,
            order,
            roles[item.analysis_identity],
            item.session.session_id,
            item.stored.run.normalization_run_id,
            item.stored.run.snapshot_bundle_id,
            item.lifecycle.lifecycle_observation_id,
        )
        for order, item in enumerate(ordered_inputs)
    ]
    insert_and_compare(
        connection,
        "episode_projection_inputs",
        input_rows,
        "episode_projection_id = ?",
        [projection_id],
    )
    candidate_rows = []
    witness_rows = []
    for state in candidates:
        parent = state.parent
        child = state.child
        child_source = child.stored.source if child is not None else state.child_source
        candidate_rows.append(
            (
                projection_id,
                state.candidate.topology_candidate_id,
                state.candidate.direction,
                state.candidate.native_spawn_identity,
                parent.stored.source.source_id if parent else None,
                parent.logical_source_id if parent else None,
                parent.snapshot_content_id if parent else None,
                child_source.source_id if child_source else None,
                child.logical_source_id if child else None,
                child.snapshot_content_id if child else None,
                state.candidate.parent_analysis_identity,
                state.candidate.child_analysis_identity,
                state.candidate.status,
                state.candidate.reason,
                state.candidate.endpoint_status,
            )
        )
        witness_rows.extend(
            (
                projection_id,
                state.candidate.topology_candidate_id,
                witness.bundle_id,
                witness.parent_snapshot_id,
                witness.child_snapshot_id,
                "tool_call" if state.spawn_entity_id else None,
                state.spawn_entity_id,
                state.spawn_anchor_id,
            )
            for witness in state.witnesses
        )
    insert_and_compare(
        connection,
        "episode_topology_candidates",
        candidate_rows,
        "episode_projection_id = ?",
        [projection_id],
    )
    insert_and_compare(
        connection,
        "episode_topology_candidate_witnesses",
        witness_rows,
        "episode_projection_id = ?",
        [projection_id],
    )
    linked = {
        state.child.analysis_identity: state
        for state in candidates
        if state.candidate.status == "linked"
        and state.child is not None
        and state.parent is not None
        and state.spawn_entity_id is not None
        and state.spawn_anchor_id is not None
        and state.parent_episode_id is not None
    }
    binding_rows = []
    binding_witness_rows = []
    delegation_rows = []
    for child_identity, state in sorted(linked.items()):
        assert state.parent is not None
        assert state.parent_episode_id is not None
        assert state.spawn_entity_id is not None
        assert state.spawn_anchor_id is not None
        witness_ids = [witness.bundle_id for witness in state.witnesses]
        binding_rows.append(
            (
                projection_id,
                child_identity,
                state.parent.analysis_identity,
                state.parent_episode_id,
                "tool_call",
                state.spawn_entity_id,
                state.spawn_anchor_id,
                TOPOLOGY_POLICY_VERSION,
                json.dumps(
                    {"witness_bundle_ids": witness_ids},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        for witness in state.witnesses:
            binding_witness_rows.append(
                (
                    projection_id,
                    child_identity,
                    witness.bundle_id,
                    state.candidate.topology_candidate_id,
                    witness.parent_snapshot_id,
                    witness.child_snapshot_id,
                    "tool_call",
                    state.spawn_entity_id,
                    state.spawn_anchor_id,
                )
            )
        for episode in analyses[child_identity].episodes:
            delegation_id = stable_id(
                "episode-delegation",
                TOPOLOGY_POLICY_VERSION,
                state.parent.analysis_identity,
                state.parent_episode_id,
                child_identity,
                episode.episode_id,
                "tool_call",
                state.spawn_entity_id,
                state.spawn_anchor_id,
                json.dumps(witness_ids, separators=(",", ":")),
            )
            delegation_rows.append(
                (
                    projection_id,
                    child_identity,
                    episode.episode_id,
                    state.parent.analysis_identity,
                    state.parent_episode_id,
                    delegation_id,
                )
            )
    for table, rows in (
        ("episode_delegation_bindings", binding_rows),
        ("episode_delegation_binding_witnesses", binding_witness_rows),
        ("episode_delegations", delegation_rows),
    ):
        insert_and_compare(
            connection,
            table,
            rows,
            "episode_projection_id = ?",
            [projection_id],
        )
    unresolved_children = {
        state.child.analysis_identity
        for state in candidates
        if state.child is not None and state.candidate.status not in {"linked", "not_child"}
    }

    def top_owner(analysis_identity: str, episode_id: str) -> tuple[str, str] | None:
        visited: set[str] = set()
        current_identity = analysis_identity
        current_episode = episode_id
        while current_identity in linked:
            if current_identity in visited:
                return None
            visited.add(current_identity)
            state = linked[current_identity]
            assert state.parent is not None and state.parent_episode_id is not None
            current_identity = state.parent.analysis_identity
            current_episode = state.parent_episode_id
        if current_identity in unresolved_children:
            return None
        return current_identity, current_episode

    memberships: list[EpisodeMembership] = []
    for item in ordered_inputs:
        for membership in derive_memberships(
            item,
            analyses[item.analysis_identity],
            projection_id,
        ):
            if membership.membership_status != "assigned":
                memberships.append(membership)
                continue
            assert membership.source_episode_id is not None
            owner = top_owner(item.analysis_identity, membership.source_episode_id)
            if owner is None:
                memberships.append(
                    membership.model_copy(
                        update={
                            "rollup_owner_status": "unavailable",
                            "rollup_owner_analysis_identity": None,
                            "rollup_owner_episode_id": None,
                            "aggregate_eligibility": "ineligible",
                            "reason": "delegation_ancestry_unavailable",
                        }
                    )
                )
            elif item.analysis_identity in linked:
                memberships.append(
                    membership.model_copy(
                        update={
                            "rollup_owner_analysis_identity": owner[0],
                            "rollup_owner_episode_id": owner[1],
                            "aggregate_eligibility": "excluded_delegated",
                            "reason": "delegated_to_top_level_owner",
                        }
                    )
                )
            else:
                memberships.append(membership)
    persist_memberships(connection, projection_id, memberships)


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
    anchor_by_raw_event_id = {
        anchor.entity_id: anchor for anchor in anchors.values() if anchor.anchor_kind == "raw_event"
    }
    anchored_event_ids = {
        anchor.entity_id
        for episode in analysis.episodes
        for anchor_id in episode.event_anchor_ids
        if (anchor := anchors[anchor_id]).anchor_kind == "raw_event"
    }
    first_anchored_record_by_source: dict[str, int] = {}
    for event_id in anchored_event_ids:
        event = raw_by_id[event_id]
        first_anchored_record_by_source[event.source_id] = min(
            event.record_index,
            first_anchored_record_by_source.get(event.source_id, event.record_index),
        )
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
        related_events: list[RawEvent] = []
        if entity_kind in {"session", "session_source"}:
            reason = "container_not_episode_evidence"
        elif entity_kind == "raw_event":
            related_events = [cast(RawEvent, entity)]
            anchor = anchor_by_raw_event_id.get(entity_id)
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
                related_events = event_matches
                for event in event_matches:
                    anchor = next(
                        row for row in anchors.values() if row.entity_id == event.event_id
                    )
                    candidates.update(episode_by_anchor.get(anchor.anchor_id, set()))
        else:
            source_event_id = getattr(entity, "source_event_id", None)
            if source_event_id in raw_by_id:
                event = raw_by_id[source_event_id]
                related_events = [event]
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
            if reason == "no_deterministic_anchor" and not analysis.episodes:
                reason = "no_episode_anchor"
            elif reason == "no_deterministic_anchor" and related_events:
                event_sources = {event.source_id for event in related_events}
                if not event_sources.intersection(first_anchored_record_by_source):
                    reason = "cross_source_partial_order"
                elif all(
                    event.record_index < first_anchored_record_by_source.get(event.source_id, -1)
                    for event in related_events
                ):
                    reason = "before_first_episode"
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
    binding_rows = connection.execute(
        """
        SELECT child_analysis_identity, parent_analysis_identity,
            parent_episode_id, spawn_entity_kind, spawn_entity_id,
            spawn_anchor_id, provenance_json
        FROM episode_delegation_bindings WHERE episode_projection_id = ?
        ORDER BY child_analysis_identity
        """,
        [projection_id],
    ).fetchall()
    bindings = [
        EpisodeDelegationBinding(
            child_analysis_identity=str(row[0]),
            parent_analysis_identity=str(row[1]),
            parent_episode_id=str(row[2]),
            spawn_entity_kind=str(row[3]),
            spawn_entity_id=str(row[4]),
            spawn_anchor_id=str(row[5]),
            witness_bundle_ids=[
                str(item) for item in json.loads(str(row[6]))["witness_bundle_ids"]
            ],
        )
        for row in binding_rows
    ]
    edge_rows = connection.execute(
        """
        SELECT delegation_id, child_analysis_identity, child_episode_id,
            parent_analysis_identity, parent_episode_id
        FROM episode_delegations WHERE episode_projection_id = ?
        ORDER BY parent_analysis_identity, parent_episode_id,
            child_analysis_identity, child_episode_id
        """,
        [projection_id],
    ).fetchall()
    edges = [
        EpisodeDelegationEdge(
            delegation_id=str(row[0]),
            child_analysis_identity=str(row[1]),
            child_episode_id=str(row[2]),
            parent_analysis_identity=str(row[3]),
            parent_episode_id=str(row[4]),
        )
        for row in edge_rows
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
        delegation=EpisodeDelegation(
            candidates=candidates,
            bindings=bindings,
            child_episode_edges=edges,
        ),
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
