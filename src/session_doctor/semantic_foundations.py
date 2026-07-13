from __future__ import annotations

import os
from pathlib import Path

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    AdapterCapabilityDeclaration,
    CapabilityEvidence,
    CausalOrderEdge,
    InstrumentationStatus,
    ModelIdentity,
    ModelIdentityState,
    OrderingProjection,
    ProjectIdentity,
    ProjectIdentityState,
    SemanticAnalysisComponents,
    SemanticFoundation,
    SourceOrderItem,
    UsageProjection,
    UsageSemantics,
)

ORDERING_VERSION = "source-order-v1"
SEMANTIC_FOUNDATION_VERSION = "semantic-foundation-v1"


def derive_semantic_foundation(
    bundle: ParsedSessionBundle,
    declarations: tuple[AdapterCapabilityDeclaration, ...],
    *,
    terminal_observed: bool,
    observed_vcs_root: str | None,
) -> SemanticFoundation:
    return SemanticFoundation(
        semantic_foundation_version=SEMANTIC_FOUNDATION_VERSION,
        ordering=derive_ordering(bundle),
        capabilities=derive_capabilities(bundle, declarations, terminal_observed),
        project_identity=derive_project_identity(bundle, observed_vcs_root),
        model_identity=derive_model_identity(bundle),
        usage=derive_usage_projection(bundle),
    )


def derive_ordering(bundle: ParsedSessionBundle) -> OrderingProjection:
    ordered_events = sorted(
        bundle.raw_events,
        key=lambda event: (event.source_id, event.record_index, event.event_id),
    )
    native_ids = {
        (event.source_id, event.native_event_id): event.event_id
        for event in ordered_events
        if event.native_event_id is not None
    }
    edges = sorted(
        (
            CausalOrderEdge(
                parent_event_id=native_ids[(event.source_id, event.native_parent_id)],
                child_event_id=event.event_id,
            )
            for event in ordered_events
            if event.native_parent_id is not None
            and (event.source_id, event.native_parent_id) in native_ids
        ),
        key=lambda edge: (edge.parent_event_id, edge.child_event_id),
    )
    return OrderingProjection(
        ordering_version=ORDERING_VERSION,
        source_order=[
            SourceOrderItem(
                event_id=event.event_id,
                source_id=event.source_id,
                record_index=event.record_index,
            )
            for event in ordered_events
        ],
        causal_edges=edges,
    )


def derive_capabilities(
    bundle: ParsedSessionBundle,
    declarations: tuple[AdapterCapabilityDeclaration, ...],
    terminal_observed: bool,
) -> list[CapabilityEvidence]:
    evidence_by_capability = {
        "native_causal_links": sorted(
            event.event_id for event in bundle.raw_events if event.native_parent_id is not None
        ),
        "terminal_evidence": ["native-terminal"] if terminal_observed else [],
        "delegation_topology": (
            [bundle.session.session_id]
            if bundle.session is not None and bundle.session.parent_session_id is not None
            else []
        ),
        "model_usage": sorted(row.model_usage_id for row in bundle.model_usage),
        "native_project_metadata": (
            [bundle.session.session_id]
            if native_repository_path(bundle) is not None and bundle.session is not None
            else []
        ),
        "native_cost": sorted(
            row.model_usage_id for row in bundle.model_usage if row.cost is not None
        ),
    }
    return [
        CapabilityEvidence(
            capability=declaration.capability,
            support=declaration.support,
            instrumentation=declaration.instrumentation,
            evidence_status=(
                InstrumentationStatus.OBSERVED
                if evidence_by_capability.get(declaration.capability)
                else InstrumentationStatus.UNAVAILABLE
            ),
            evidence_ids=evidence_by_capability.get(declaration.capability, []),
        )
        for declaration in sorted(declarations, key=lambda row: row.capability)
    ]


def derive_project_identity(
    bundle: ParsedSessionBundle, observed_vcs_root: str | None
) -> ProjectIdentity:
    native_path = native_repository_path(bundle)
    if native_path is not None:
        return ProjectIdentity(
            state=ProjectIdentityState.NATIVE_REPOSITORY,
            path=native_path,
            evidence="native_session_metadata",
        )
    if observed_vcs_root is not None:
        return ProjectIdentity(
            state=ProjectIdentityState.OBSERVED_VCS_ROOT,
            path=observed_vcs_root,
            evidence="ingestion_filesystem_observation",
        )
    if bundle.session is not None and bundle.session.cwd:
        return ProjectIdentity(
            state=ProjectIdentityState.STORED_CWD,
            path=os.path.normpath(bundle.session.cwd),
            evidence="stored_session_cwd",
        )
    return ProjectIdentity(
        state=ProjectIdentityState.UNKNOWN,
        path=None,
        evidence="unavailable",
    )


def native_repository_path(bundle: ParsedSessionBundle) -> str | None:
    if bundle.session is None:
        return None
    for key in ("repository_root", "repo_root", "repository_path"):
        value = bundle.session.metadata.get(key)
        if isinstance(value, str) and value and Path(value).is_absolute():
            return os.path.normpath(value)
    return None


def observe_vcs_root(cwd: str | None) -> str | None:
    if not cwd:
        return None
    candidate = Path(cwd).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    for directory in (candidate, *candidate.parents):
        try:
            if (directory / ".git").exists():
                return str(directory)
        except OSError:
            return None
    return None


def derive_model_identity(bundle: ParsedSessionBundle) -> ModelIdentity:
    models: set[str] = set()
    if bundle.session is not None:
        if bundle.session.model:
            models.add(bundle.session.model)
        metadata_models = bundle.session.metadata.get("models")
        if isinstance(metadata_models, list):
            models.update(value for value in metadata_models if isinstance(value, str) and value)
    models.update(row.model for row in bundle.model_usage if row.model)
    models.update(
        value
        for message in bundle.messages
        if isinstance((value := message.metadata.get("model")), str) and value
    )
    ordered_models = sorted(models)
    state = (
        ModelIdentityState.UNKNOWN
        if not ordered_models
        else ModelIdentityState.ONE_MODEL
        if len(ordered_models) == 1
        else ModelIdentityState.MIXED_MODELS
    )
    return ModelIdentity(state=state, models=ordered_models)


def derive_usage_projection(bundle: ParsedSessionBundle) -> UsageProjection:
    row_semantics = {row.model_usage_id: row.aggregation_semantics for row in bundle.model_usage}
    semantics = set(row_semantics.values())
    aggregation = (
        next(iter(semantics)) if len(semantics) == 1 else UsageSemantics.AGGREGATION_UNAVAILABLE
    )
    return UsageProjection(aggregation=aggregation, row_semantics=row_semantics)


def semantic_analysis_identity(components: SemanticAnalysisComponents) -> str:
    return stable_id(
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
    )
