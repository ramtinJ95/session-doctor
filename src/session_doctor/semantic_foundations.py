from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from session_doctor.adapters import ParsedSessionBundle
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
    terminal_evidence_ids: tuple[str, ...] = (),
) -> SemanticFoundation:
    return SemanticFoundation(
        semantic_foundation_version=SEMANTIC_FOUNDATION_VERSION,
        ordering=derive_ordering(bundle),
        capabilities=derive_capabilities(
            bundle,
            declarations,
            terminal_observed,
            terminal_evidence_ids,
        ),
        project_identity=derive_project_identity(bundle, observed_vcs_root),
        model_identity=derive_model_identity(bundle),
        usage=derive_usage_projection(bundle),
    )


def derive_ordering(bundle: ParsedSessionBundle) -> OrderingProjection:
    source_indexes: set[tuple[str, int]] = set()
    for event in bundle.raw_events:
        source_index = (event.source_id, event.record_index)
        if source_index in source_indexes:
            raise ValueError(
                f"duplicate source record index: {event.source_id}:{event.record_index}"
            )
        source_indexes.add(source_index)
    ordered_events = sorted(
        bundle.raw_events,
        key=lambda event: (event.source_id, event.record_index),
    )
    native_id_groups: dict[tuple[str, str], list[str]] = {}
    for event in ordered_events:
        if event.native_event_id is not None:
            native_id_groups.setdefault((event.source_id, event.native_event_id), []).append(
                event.event_id
            )
    native_ids = {
        key: event_ids[0] for key, event_ids in native_id_groups.items() if len(event_ids) == 1
    }
    ambiguous_native_ids = sorted(
        native_id
        for (_source_id, native_id), event_ids in native_id_groups.items()
        if len(event_ids) > 1
    )
    unresolved_parent_event_ids = sorted(
        event.event_id
        for event in ordered_events
        if event.native_parent_id is not None
        and (event.source_id, event.native_parent_id) not in native_ids
    )
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
        ambiguous_native_event_ids=ambiguous_native_ids,
        unresolved_parent_event_ids=unresolved_parent_event_ids,
    )


def derive_capabilities(
    bundle: ParsedSessionBundle,
    declarations: tuple[AdapterCapabilityDeclaration, ...],
    terminal_observed: bool,
    terminal_evidence_ids: tuple[str, ...] = (),
) -> list[CapabilityEvidence]:
    ordering = derive_ordering(bundle)
    evidence_by_capability = {
        "native_causal_links": sorted(edge.child_event_id for edge in ordering.causal_edges),
        "terminal_evidence": (sorted(set(terminal_evidence_ids)) if terminal_observed else []),
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
            models.add(model_identity_key(bundle.session.model_provider, bundle.session.model))
        metadata_models = bundle.session.metadata.get("models")
        if isinstance(metadata_models, list):
            for value in metadata_models:
                add_model_value(models, value)
        model_changes = bundle.session.metadata.get("model_changes")
        if isinstance(model_changes, list):
            for value in model_changes:
                add_model_value(models, value)
    models.update(
        model_identity_key(row.provider, row.model) for row in bundle.model_usage if row.model
    )
    for message in bundle.messages:
        model = message.metadata.get("model")
        provider = message.metadata.get("provider")
        if isinstance(model, str) and model:
            models.add(
                model_identity_key(
                    provider if isinstance(provider, str) else None,
                    model,
                )
            )
    models = {
        value
        for value in models
        if "/" in value or not any(other.endswith(f"/{value}") for other in models)
    }
    ordered_models = sorted(models)
    state = (
        ModelIdentityState.UNKNOWN
        if not ordered_models
        else ModelIdentityState.ONE_MODEL
        if len(ordered_models) == 1
        else ModelIdentityState.MIXED_MODELS
    )
    return ModelIdentity(state=state, models=ordered_models)


def add_model_value(models: set[str], value: object) -> None:
    if isinstance(value, str) and value:
        models.add(value)
        return
    if not isinstance(value, Mapping):
        return
    mapping = cast(Mapping[str, object], value)
    model = mapping.get("model")
    provider = mapping.get("provider")
    if isinstance(model, str) and model:
        models.add(
            model_identity_key(
                provider if isinstance(provider, str) else None,
                model,
            )
        )


def model_identity_key(provider: str | None, model: str) -> str:
    return f"{provider}/{model}" if provider else model


def derive_usage_projection(bundle: ParsedSessionBundle) -> UsageProjection:
    row_semantics = {row.model_usage_id: row.aggregation_semantics for row in bundle.model_usage}
    semantics = set(row_semantics.values())
    aggregation = (
        next(iter(semantics)) if len(semantics) == 1 else UsageSemantics.AGGREGATION_UNAVAILABLE
    )
    return UsageProjection(aggregation=aggregation, row_semantics=row_semantics)


def semantic_analysis_identity(components: SemanticAnalysisComponents) -> str:
    payload = json.dumps(
        components.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
