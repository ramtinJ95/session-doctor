from __future__ import annotations

from datetime import UTC, datetime

import pytest

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.schemas import (
    AdapterCapabilityDeclaration,
    AgentName,
    CapabilitySupport,
    ModelIdentityState,
    ModelUsage,
    ProjectIdentityState,
    RawEvent,
    SemanticAnalysisComponents,
    Session,
    SessionSource,
    UsageSemantics,
)
from session_doctor.semantic_foundations import (
    ORDERING_VERSION,
    derive_capabilities,
    derive_model_identity,
    derive_ordering,
    derive_project_identity,
    derive_usage_projection,
    observe_vcs_root,
    semantic_analysis_identity,
)
from session_doctor.store import DuckDBStore


def raw_event(
    event_id: str,
    source_id: str,
    record_index: int,
    timestamp: datetime,
    *,
    native_event_id: str | None = None,
    native_parent_id: str | None = None,
) -> RawEvent:
    return RawEvent(
        event_id=event_id,
        source_id=source_id,
        agent_name=AgentName.CODEX,
        record_index=record_index,
        timestamp=timestamp,
        native_event_id=native_event_id,
        native_parent_id=native_parent_id,
    )


def test_source_record_order_ignores_timestamp_regression() -> None:
    later = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
    earlier = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    bundle = ParsedSessionBundle(
        raw_events=[
            raw_event("event-1", "source-1", 1, earlier, native_parent_id="native-0"),
            raw_event(
                "event-0",
                "source-1",
                0,
                later,
                native_event_id="native-0",
            ),
        ]
    )

    ordering = derive_ordering(bundle)

    assert ordering.ordering_version == ORDERING_VERSION
    assert [row.event_id for row in ordering.source_order] == ["event-0", "event-1"]
    assert [(edge.parent_event_id, edge.child_event_id) for edge in ordering.causal_edges] == [
        ("event-0", "event-1")
    ]


def test_concurrent_sources_remain_partial_order() -> None:
    observed_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    ordering = derive_ordering(
        ParsedSessionBundle(
            raw_events=[
                raw_event("child", "source-b", 0, observed_at, native_parent_id="root"),
                raw_event("root", "source-a", 0, observed_at, native_event_id="root"),
            ]
        )
    )

    assert ordering.cross_source_order == "partial_order"
    assert ordering.causal_edges == []


def test_ambiguous_native_ids_and_duplicate_source_indexes_do_not_guess() -> None:
    observed_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    ambiguous = ParsedSessionBundle(
        raw_events=[
            raw_event("first", "source-1", 0, observed_at, native_event_id="shared"),
            raw_event("second", "source-1", 1, observed_at, native_event_id="shared"),
            raw_event("child", "source-1", 2, observed_at, native_parent_id="shared"),
        ]
    )
    ordering = derive_ordering(ambiguous)
    assert ordering.causal_edges == []
    assert ordering.ambiguous_native_event_ids == ["shared"]
    assert ordering.unresolved_parent_event_ids == ["child"]

    duplicate_index = ParsedSessionBundle(
        raw_events=[
            raw_event("first", "source-1", 0, observed_at),
            raw_event("second", "source-1", 0, observed_at),
        ]
    )
    with pytest.raises(ValueError, match="duplicate source record index"):
        derive_ordering(duplicate_index)


def test_capability_support_does_not_turn_missing_evidence_into_zero() -> None:
    declaration = AdapterCapabilityDeclaration(
        capability="model_usage",
        support=CapabilitySupport.SUPPORTED,
        instrumentation="native",
    )

    evidence = derive_capabilities(ParsedSessionBundle(), (declaration,), False)

    assert evidence[0].support is CapabilitySupport.SUPPORTED
    assert evidence[0].evidence_status == "unavailable"
    assert evidence[0].evidence_ids == []
    unresolved = ParsedSessionBundle(
        raw_events=[
            raw_event(
                "child",
                "source-1",
                0,
                datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
                native_parent_id="missing",
            )
        ]
    )
    causal_declaration = declaration.model_copy(update={"capability": "native_causal_links"})
    causal = derive_capabilities(unresolved, (causal_declaration,), False)
    assert causal[0].evidence_status == "unavailable"


def test_project_identity_precedence_and_observed_vcs_root(tmp_path) -> None:
    repository = tmp_path / "repo"
    cwd = repository / "packages" / "app"
    cwd.mkdir(parents=True)
    (repository / ".git").mkdir()
    observed = observe_vcs_root(str(cwd))
    assert observed == str(repository)
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
        cwd=str(cwd),
    )
    observed_identity = derive_project_identity(ParsedSessionBundle(session=session), observed)
    assert observed_identity.state is ProjectIdentityState.OBSERVED_VCS_ROOT
    assert observed_identity.path == str(repository)

    native_session = session.model_copy(
        update={"metadata": {"repository_root": "/native/repository"}}
    )
    native_identity = derive_project_identity(ParsedSessionBundle(session=native_session), observed)
    assert native_identity.state is ProjectIdentityState.NATIVE_REPOSITORY
    assert native_identity.path == "/native/repository"


def test_ingestion_observed_vcs_identity_is_persisted(tmp_path) -> None:
    repository = tmp_path / "repo"
    cwd = repository / "src"
    cwd.mkdir(parents=True)
    (repository / ".git").mkdir()
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/sessions/source-1.jsonl",
    )
    captured = store.capture_source(source, b"{}\n")
    captured_bundle = store.create_single_source_bundle(
        source,
        captured,
        source.source_id,
        capture_evidence={"observed_vcs_root": str(repository)},
    )
    store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=False)
    bundle = ParsedSessionBundle(
        session=Session(
            session_id="session-1",
            source_id=source.source_id,
            agent_name=source.agent_name,
            cwd=str(cwd),
        )
    )
    store.insert_parsed_bundle(
        source,
        bundle,
        captured,
        captured_bundle,
        capability_declarations=CodexAdapter.capabilities,
    )
    coverage = store.normalization_coverage(
        captured_bundle.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    assert coverage.current_normalization_run_id is not None

    foundation = store.load_semantic_foundation(coverage.current_normalization_run_id)

    assert foundation is not None
    assert foundation.project_identity.state is ProjectIdentityState.OBSERVED_VCS_ROOT
    assert foundation.project_identity.path == str(repository)


def test_model_and_usage_states_preserve_mixed_and_unavailable() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
        model="model-a",
        model_provider="provider-a",
        metadata={
            "model_changes": [
                {"provider": "provider-b", "model": "model-b"},
            ]
        },
    )
    cumulative = ModelUsage(
        model_usage_id="usage-1",
        session_id=session.session_id,
        model="model-b",
        provider="provider-b",
        aggregation_semantics=UsageSemantics.CUMULATIVE,
    )
    incremental = ModelUsage(
        model_usage_id="usage-2",
        session_id=session.session_id,
        model="model-a",
        aggregation_semantics=UsageSemantics.INCREMENTAL,
    )
    bundle = ParsedSessionBundle(
        session=session,
        model_usage=[cumulative, incremental],
    )

    model_identity = derive_model_identity(bundle)
    usage = derive_usage_projection(bundle)

    assert model_identity.state is ModelIdentityState.MIXED_MODELS
    assert model_identity.models == ["provider-a/model-a", "provider-b/model-b"]
    assert usage.aggregation is UsageSemantics.AGGREGATION_UNAVAILABLE
    assert (
        derive_usage_projection(ParsedSessionBundle()).aggregation
        is UsageSemantics.AGGREGATION_UNAVAILABLE
    )


def test_semantic_analysis_identity_is_deterministic_and_history_is_additive(tmp_path) -> None:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/sessions/source-1.jsonl",
    )
    captured = store.capture_source(source, b"{}\n")
    captured_bundle = store.create_single_source_bundle(source, captured, source.source_id)
    lifecycle = store.record_lifecycle(
        captured_bundle.snapshot_bundle_id,
        terminal_observed=False,
    )
    store.insert_parsed_bundle(
        source,
        ParsedSessionBundle(),
        captured,
        captured_bundle,
    )
    coverage = store.normalization_coverage(
        captured_bundle.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version="0.1.0",
    )
    assert coverage.current_normalization_run_id is not None
    base = SemanticAnalysisComponents(
        normalization_run_id=coverage.current_normalization_run_id,
        lifecycle_observation_id=lifecycle.lifecycle_observation_id,
        lifecycle_policy_version="lifecycle-v1",
        ordering_version=ORDERING_VERSION,
        segmentation_version="segmentation-v1",
        relation_rule_set_version="relations-v1",
        result_rule_set_version="results-v1",
        finding_rule_set_version="findings-v1",
        facet_policy_version="facets-v1",
        configuration_hash="configuration-1",
    )
    changed = base.model_copy(update={"segmentation_version": "segmentation-v2"})
    assert semantic_analysis_identity(base) == semantic_analysis_identity(base)
    assert semantic_analysis_identity(base) != semantic_analysis_identity(changed)
    first = store.record_semantic_analysis_run(base)
    repeated = store.record_semantic_analysis_run(base)
    second = store.record_semantic_analysis_run(changed)

    assert first.analysis_identity == repeated.analysis_identity
    assert first.analysis_identity != second.analysis_identity
    assert [run.analysis_identity for run in store.list_semantic_analysis_runs()] == sorted(
        [first.analysis_identity, second.analysis_identity]
    )
