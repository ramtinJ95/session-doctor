from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.adapters.codex import CodexAdapter
from session_doctor.cli import app
from session_doctor.evaluation_models import (
    AuditSelectionStatus,
    BoundaryPacket,
    EvaluationPacketExport,
    HumanAdjudication,
    HumanReviewKind,
    IdentityExposureStatus,
    JudgeAnnotation,
    JudgeConsensusStatus,
    ReferenceResolutionStatus,
)
from session_doctor.evaluation_packets import (
    boundary_pilot_corpus_bytes,
    canonical_json,
    export_boundary_packets,
    export_boundary_pilot,
    load_boundary_pilot,
)
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    AgentName,
    Message,
    ModelIdentity,
    ModelIdentityState,
    ModelReference,
    NormalizedRole,
    RawEvent,
    Session,
    SessionSource,
    ToolCall,
    ToolResult,
)
from session_doctor.store import (
    DuckDBStore,
    EvaluationImportError,
    SnapshotPruneBlocked,
    create_reference_resolution,
    freeze_audit_protocol,
    import_human_adjudication,
    import_judge_annotation,
    register_evaluation_corpus,
    resolve_judge_panel,
    select_panel_audit,
)
from session_doctor.store.migrations import SCHEMA_VERSION, rebuild_derived_schema

runner = CliRunner()


def first_audit_packet(seed: str, exports: tuple[EvaluationPacketExport, ...]) -> str:
    return min(
        (row.routing.packet_id for row in exports),
        key=lambda packet_id: hashlib.sha256(f"{seed}\0{packet_id}".encode()).hexdigest(),
    )


def evaluation_fixture(
    tmp_path,
    *,
    target_provider: str = "target-provider",
    target_model: str = "target-model",
) -> tuple[DuckDBStore, tuple[EvaluationPacketExport, ...], str]:
    store = DuckDBStore(tmp_path / "session-doctor.duckdb")
    source = SessionSource(
        source_id="source-1",
        agent_name=AgentName.CODEX,
        source_path="/sessions/source-1.jsonl",
    )
    captured = store.capture_source(source, b"{}\n")
    captured_bundle = store.create_single_source_bundle(source, captured, "native-1")
    store.record_lifecycle(captured_bundle.snapshot_bundle_id, terminal_observed=False)
    session = Session(
        session_id="session-1",
        source_id=source.source_id,
        agent_name=source.agent_name,
        native_session_id="native-1",
        model_provider=target_provider,
        model=target_model,
    )
    events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id=source.source_id,
            agent_name=source.agent_name,
            record_index=index,
        )
        for index in range(11)
    ]
    roles = [
        NormalizedRole.USER if index % 2 == 0 else NormalizedRole.ASSISTANT for index in range(11)
    ]
    messages = [
        Message(
            message_id=f"message-{index}",
            session_id=session.session_id,
            source_event_id=events[index].event_id,
            role=role,
            text=(
                "Ask target-provider target-model via codex" if index == 0 else f"message {index}"
            ),
        )
        for index, role in enumerate(roles)
    ]
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=events,
        messages=messages,
        tool_calls=[
            ToolCall(
                tool_call_id="tool-call-1",
                session_id=session.session_id,
                source_event_id=events[1].event_id,
                name="shell",
            )
        ],
        tool_results=[
            ToolResult(
                tool_result_id="tool-result-1",
                session_id=session.session_id,
                source_event_id=events[1].event_id,
                tool_call_id="tool-call-1",
                is_error=False,
            )
        ],
    )
    store.insert_parsed_bundle(
        source,
        bundle,
        captured,
        captured_bundle,
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    coverage = store.normalization_coverage(
        captured_bundle.snapshot_bundle_id,
        adapter_name="codex",
        adapter_version=CodexAdapter.version,
        capability_declarations=CodexAdapter.capabilities,
    )
    assert coverage.current_normalization_run_id is not None
    stored = store.load_normalization(coverage.current_normalization_run_id)
    foundation = store.load_semantic_foundation(coverage.current_normalization_run_id)
    assert stored is not None
    assert foundation is not None
    exports = export_boundary_packets(stored, foundation)
    register_evaluation_corpus(
        store.database_path,
        coverage.current_normalization_run_id,
        exports,
    )
    return store, exports, coverage.current_normalization_run_id


def annotation(
    packet_id: str,
    evidence_id: str,
    judge_index: int,
    answer: str,
) -> JudgeAnnotation:
    return JudgeAnnotation(
        judge_annotation_id=stable_id("judge", packet_id, judge_index, answer),
        packet_id=packet_id,
        judge_model=f"judge-model-{judge_index}",
        judge_provider=f"judge-provider-{judge_index}",
        judge_prompt_version="boundary-prompt-v1",
        answer=answer,
        evidence_ids=[evidence_id],
        rationale=f"rationale {judge_index}",
        created_at=datetime(2026, 7, 13, 12, judge_index % 60, tzinfo=UTC),
    )


def test_boundary_packet_export_is_deterministic_blinded_and_preseal(tmp_path) -> None:
    store, exports, normalization_run_id = evaluation_fixture(tmp_path)
    assert len(exports) == 5
    first = exports[0]
    assert isinstance(first.judge_packet, BoundaryPacket)
    stored = store.load_normalization(normalization_run_id)
    assert stored is not None
    foundation = store.load_semantic_foundation(stored.run.normalization_run_id)
    assert foundation is not None
    assert export_boundary_packets(stored, foundation) == exports
    judge_json = canonical_json(first.judge_packet.model_dump(mode="json"))
    routing_json = canonical_json(first.routing.model_dump(mode="json"))
    assert "target-provider" not in judge_json
    assert "target-model" not in judge_json
    assert '"codex"' not in judge_json
    assert "target-model" in routing_json
    assert first.routing.identity_exposure_status is IdentityExposureStatus.BLIND_ELIGIBLE
    assert first.routing.source_family_id is None
    assert first.routing.family_policy_version is None
    assert first.routing.source_family_status == "unknown"
    assert first.judge_packet.left_user_event_id.startswith("ev_")
    assert "event-0" not in judge_json
    assert first.judge_packet.right_user_event_id.startswith("ev_")
    assert [event.entity_kind for event in first.judge_packet.intervening_normalized_events] == [
        "message",
        "tool_call",
        "tool_result",
    ]
    redacted_text = "Ask [identity_redacted] [identity_redacted] via [identity_redacted]"
    assert (
        first.judge_packet.adjacent_user_turns[0].text_hash
        == hashlib.sha256(redacted_text.encode()).hexdigest()
    )
    unknown_foundation = foundation.model_copy(
        update={
            "model_identity": ModelIdentity(
                state=ModelIdentityState.UNKNOWN,
                models=[],
            )
        }
    )
    assert (
        export_boundary_packets(stored, unknown_foundation)[0].routing.identity_exposure_status
        is IdentityExposureStatus.TARGET_IDENTITY_UNVERIFIABLE
    )
    exposed_foundation = foundation.model_copy(
        update={
            "model_identity": ModelIdentity(
                state=ModelIdentityState.ONE_MODEL,
                models=[ModelReference(provider="p", model="m")],
            )
        }
    )
    assert (
        export_boundary_packets(stored, exposed_foundation)[0].routing.identity_exposure_status
        is IdentityExposureStatus.IDENTITY_EXPOSED
    )
    messages_without_source = list(stored.bundle.messages)
    messages_without_source[2] = messages_without_source[2].model_copy(
        update={"source_event_id": None}
    )
    messages_without_source[1] = messages_without_source[1].model_copy(
        update={"source_event_id": "missing-event"}
    )
    unresolved_stored = replace(
        stored,
        bundle=stored.bundle.model_copy(update={"messages": messages_without_source}),
    )
    unresolved_exports = export_boundary_packets(unresolved_stored, foundation)
    unresolved_packet = unresolved_exports[0].judge_packet
    assert isinstance(unresolved_packet, BoundaryPacket)
    assert unresolved_packet.adjacent_user_turns[1].source_event_id is None
    assert (
        unresolved_packet.right_user_event_id
        == unresolved_packet.adjacent_user_turns[1].evidence_id
    )
    unresolved_boundary_packets = []
    for exported in unresolved_exports:
        assert isinstance(exported.judge_packet, BoundaryPacket)
        unresolved_boundary_packets.append(exported.judge_packet)
    unresolved_message = next(
        event
        for packet in unresolved_boundary_packets
        for event in (packet.intervening_normalized_events + packet.bounded_context_events)
        if event.text == "message 1"
    )
    assert unresolved_message.source_event_id is None

    pi_messages = list(stored.bundle.messages)
    pi_messages[0] = pi_messages[0].model_copy(
        update={"text": "Use pi but preserve compile output"}
    )
    pi_stored = replace(
        stored,
        run=replace(stored.run, adapter_name="pi"),
        bundle=stored.bundle.model_copy(update={"messages": pi_messages}),
    )
    pi_packet_json = canonical_json(
        export_boundary_packets(pi_stored, foundation)[0].judge_packet.model_dump(mode="json")
    )
    assert "Use [identity_redacted] but preserve compile output" in pi_packet_json


def test_judge_import_rejects_hallucinated_evidence_and_target_judge(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    evidence_id = packet.judge_packet.left_user_event_id
    valid = annotation(packet.routing.packet_id, evidence_id, 1, "split")
    assert import_judge_annotation(store.database_path, valid) == valid
    intervening_source_id = packet.judge_packet.intervening_normalized_events[0].source_event_id
    assert intervening_source_id is not None
    source_citation = annotation(
        packet.routing.packet_id,
        intervening_source_id,
        8,
        "split",
    )
    assert import_judge_annotation(store.database_path, source_citation) == source_citation

    invalid_evidence = valid.model_copy(
        update={
            "judge_annotation_id": "invalid-evidence",
            "evidence_ids": ["hallucinated-event"],
        }
    )
    with pytest.raises(EvaluationImportError, match="outside packet"):
        import_judge_annotation(store.database_path, invalid_evidence)

    target_judge = valid.model_copy(
        update={
            "judge_annotation_id": "target-judge",
            "judge_provider": "target-provider",
            "judge_model": "target-model",
        }
    )
    with pytest.raises(EvaluationImportError, match="cannot judge"):
        import_judge_annotation(store.database_path, target_judge)
    target_variant = target_judge.model_copy(
        update={
            "judge_annotation_id": "target-variant",
            "judge_provider": " TARGET-PROVIDER ",
            "judge_model": "Target-Model",
        }
    )
    with pytest.raises(EvaluationImportError, match="cannot judge"):
        import_judge_annotation(store.database_path, target_variant)
    wrong_protocol = valid.model_copy(
        update={
            "judge_annotation_id": "wrong-protocol",
            "annotation_protocol_version": "other-protocol",
        }
    )
    with pytest.raises(EvaluationImportError, match="protocol"):
        import_judge_annotation(store.database_path, wrong_protocol)


def test_packet_contract_rejects_provenance_and_mutable_discriminators(tmp_path) -> None:
    store, exports, normalization_run_id = evaluation_fixture(tmp_path)
    packet = exports[0]
    invalid_routing = packet.routing.model_copy(update={"normalization_run_id": "other-run"})
    with pytest.raises(EvaluationImportError, match="stored normalization"):
        register_evaluation_corpus(
            store.database_path,
            normalization_run_id,
            (
                packet.model_copy(update={"routing": invalid_routing}),
                *exports[1:],
            ),
        )
    with pytest.raises(ValidationError):
        BoundaryPacket.model_validate(
            {**packet.judge_packet.model_dump(mode="json"), "packet_kind": "episode"}
        )
    with pytest.raises(ValidationError):
        BoundaryPacket.model_validate(
            {**packet.judge_packet.model_dump(mode="json"), "allowed_answers": ["invented"]}
        )


def test_panel_requires_a_complete_frozen_corpus(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    judge = annotation(
        packet.routing.packet_id,
        packet.judge_packet.left_user_event_id,
        7,
        "split",
    )
    import_judge_annotation(store.database_path, judge)
    with pytest.raises(EvaluationImportError, match="frozen before panel"):
        resolve_judge_panel(
            store.database_path,
            packet.routing.packet_id,
            (judge.judge_annotation_id,),
        )


def test_panel_consensus_audit_and_reference_records_remain_separate(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    evidence_id = packet.judge_packet.left_user_event_id
    seed = next(
        f"seed-{index}"
        for index in range(100)
        if first_audit_packet(f"seed-{index}", exports) != packet.routing.packet_id
    )
    protocol = freeze_audit_protocol(store.database_path, packet.routing.evaluation_corpus_id, seed)
    assert len(protocol.eligible_packet_ids) == 5
    assert len(protocol.selected_packet_ids) == 1
    annotations = tuple(
        annotation(packet.routing.packet_id, evidence_id, index, "split") for index in range(3)
    )
    for row in annotations:
        import_judge_annotation(store.database_path, row)
    panel = resolve_judge_panel(
        store.database_path,
        packet.routing.packet_id,
        tuple(row.judge_annotation_id for row in annotations),
        resolved_at=datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
    )
    assert panel.consensus_status is JudgeConsensusStatus.UNANIMOUS
    audit = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
        selected_at=datetime(2026, 7, 13, 13, 1, tzinfo=UTC),
    )
    repeated = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
        selected_at=datetime(2026, 7, 13, 13, 1, tzinfo=UTC),
    )
    assert audit == repeated
    assert audit.selection_status is AuditSelectionStatus.NOT_SELECTED
    reference = create_reference_resolution(
        store.database_path,
        panel.judge_panel_resolution_id,
        resolved_at=datetime(2026, 7, 13, 13, 2, tzinfo=UTC),
    )
    assert reference.resolution_status is ReferenceResolutionStatus.JUDGE_CONSENSUS
    assert store.table_count("judge_annotations") == 3
    assert store.table_count("judge_panel_resolutions") == 1
    assert store.table_count("audit_selections") == 1
    assert store.table_count("human_adjudications") == 0
    assert store.table_count("reference_resolutions") == 1
    insufficient_packet = exports[1]
    assert isinstance(insufficient_packet.judge_packet, BoundaryPacket)
    single = annotation(
        insufficient_packet.routing.packet_id,
        insufficient_packet.judge_packet.left_user_event_id,
        99,
        "ambiguous",
    )
    import_judge_annotation(store.database_path, single)
    insufficient = resolve_judge_panel(
        store.database_path,
        insufficient_packet.routing.packet_id,
        (single.judge_annotation_id,),
    )
    assert insufficient.consensus_status is JudgeConsensusStatus.INSUFFICIENT


def test_disputed_panel_requires_compatible_human_adjudication(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    freeze_audit_protocol(
        store.database_path,
        exports[0].routing.evaluation_corpus_id,
        "disputed-seed",
    )
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    evidence_id = packet.judge_packet.left_user_event_id
    answers = ("split", "split", "no_split")
    annotations = tuple(
        annotation(packet.routing.packet_id, evidence_id, index, answer)
        for index, answer in enumerate(answers)
    )
    for row in annotations:
        import_judge_annotation(store.database_path, row)
    panel = resolve_judge_panel(
        store.database_path,
        packet.routing.packet_id,
        tuple(row.judge_annotation_id for row in annotations),
    )
    assert panel.consensus_status is JudgeConsensusStatus.DISPUTED
    with pytest.raises(EvaluationImportError, match="requires human"):
        create_reference_resolution(store.database_path, panel.judge_panel_resolution_id)
    human = HumanAdjudication(
        human_adjudication_id="human-1",
        packet_id=packet.routing.packet_id,
        judge_panel_resolution_id=panel.judge_panel_resolution_id,
        review_kind=HumanReviewKind.PANEL_DISPUTE,
        reviewer_identity="reviewer-1",
        answer="ambiguous",
        evidence_ids=[evidence_id],
        rationale="conflicting evidence",
        reviewed_at=datetime(2026, 7, 13, 14, 0, tzinfo=UTC),
    )
    import_human_adjudication(store.database_path, human)
    invalid_schema = human.model_copy(
        update={
            "human_adjudication_id": "human-invalid-schema",
            "schema_version": "other-schema",
        }
    )
    with pytest.raises(EvaluationImportError, match="schema"):
        import_human_adjudication(store.database_path, invalid_schema)
    reference = create_reference_resolution(
        store.database_path,
        panel.judge_panel_resolution_id,
    )
    assert reference.resolution_status is ReferenceResolutionStatus.AMBIGUOUS
    assert reference.source_human_adjudication_id == human.human_adjudication_id


def test_selected_consensus_audit_requires_human_resolution(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    packet = exports[1]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    evidence_id = packet.judge_packet.left_user_event_id
    seed = next(
        f"selected-{index}"
        for index in range(100)
        if first_audit_packet(f"selected-{index}", exports) == packet.routing.packet_id
    )
    freeze_audit_protocol(store.database_path, packet.routing.evaluation_corpus_id, seed)
    annotations = tuple(
        annotation(packet.routing.packet_id, evidence_id, index + 10, "no_split")
        for index in range(3)
    )
    for row in annotations:
        import_judge_annotation(store.database_path, row)
    panel = resolve_judge_panel(
        store.database_path,
        packet.routing.packet_id,
        tuple(row.judge_annotation_id for row in annotations),
    )
    audit = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
    )
    assert audit.selection_status is AuditSelectionStatus.SELECTED
    with pytest.raises(EvaluationImportError, match="requires human"):
        create_reference_resolution(store.database_path, panel.judge_panel_resolution_id)
    human = HumanAdjudication(
        human_adjudication_id="audit-human",
        packet_id=packet.routing.packet_id,
        judge_panel_resolution_id=panel.judge_panel_resolution_id,
        audit_selection_id=audit.audit_selection_id,
        review_kind=HumanReviewKind.CONSENSUS_AUDIT,
        reviewer_identity="reviewer-2",
        answer="no_split",
        evidence_ids=[evidence_id],
        rationale="consensus confirmed",
        reviewed_at=datetime(2026, 7, 13, 15, 0, tzinfo=UTC),
    )
    import_human_adjudication(store.database_path, human)
    resolution = create_reference_resolution(
        store.database_path,
        panel.judge_panel_resolution_id,
    )
    assert resolution.resolution_status is ReferenceResolutionStatus.HUMAN_RESOLVED
    assert resolution.answer == "no_split"
    assert resolution.source_audit_selection_id == audit.audit_selection_id


def test_identity_exposed_packet_is_ineligible_for_audit(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(
        tmp_path,
        target_provider="p",
        target_model="m",
    )
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    assert packet.routing.identity_exposure_status is IdentityExposureStatus.IDENTITY_EXPOSED
    evidence_id = packet.judge_packet.left_user_event_id
    freeze_audit_protocol(
        store.database_path,
        packet.routing.evaluation_corpus_id,
        "forced-eligible-seed",
    )
    annotations = tuple(
        annotation(packet.routing.packet_id, evidence_id, index + 20, "split") for index in range(3)
    )
    for row in annotations:
        import_judge_annotation(store.database_path, row)
    panel = resolve_judge_panel(
        store.database_path,
        packet.routing.packet_id,
        tuple(row.judge_annotation_id for row in annotations),
    )
    audit = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
    )
    assert audit.eligibility_status == "ineligible"
    assert audit.selection_status is AuditSelectionStatus.NOT_SELECTED
    with pytest.raises(EvaluationImportError, match="ineligible"):
        create_reference_resolution(store.database_path, panel.judge_panel_resolution_id)


def test_pilot_manifest_has_stratified_preseal_cases() -> None:
    manifest_path = Path("src/session_doctor/evaluation_data/boundary-pilot-v1.json")
    manifest = json.loads(manifest_path.read_text())
    cases = manifest["cases"]
    assert 20 <= len(cases) <= 30
    assert {row["source_id"].split("-")[0] for row in cases} == {"claude", "codex", "pi"}
    assert manifest["family_status"] == "unknown_or_ambiguous_pre_pr12"
    strata = {value for row in cases for value in row["strata"]}
    assert {"active", "blocker", "incomplete", "prior_disagreement", "success"} <= strata
    packets = load_boundary_pilot(manifest_path)
    assert len(packets) == 24
    assert len({packet.packet_id for packet in packets}) == 24
    for case, packet in zip(cases, packets, strict=True):
        event_count = (
            len(packet.adjacent_user_turns)
            + len(packet.intervening_normalized_events)
            + len(packet.bounded_context_events)
        )
        if "short" in case["strata"]:
            assert event_count == 3
        if "medium" in case["strata"]:
            assert event_count >= 7
        if "long" in case["strata"]:
            assert event_count >= 17
        if "task_switch" in case["strata"]:
            packet_text = " ".join(
                event.text or ""
                for event in packet.adjacent_user_turns + packet.bounded_context_events
            )
            assert "schema" in packet_text or "documentation" in packet_text
            assert any(term in packet_text for term in ("checks", "tests", "implementation"))
    pilot_exports = export_boundary_pilot(boundary_pilot_corpus_bytes(), "pilot-bundle-test")
    assert all(
        row.routing.identity_exposure_status is IdentityExposureStatus.TARGET_IDENTITY_UNVERIFIABLE
        for row in pilot_exports
    )


def test_snapshot_prune_reports_and_removes_evaluation_dependencies(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    freeze_audit_protocol(
        store.database_path,
        exports[0].routing.evaluation_corpus_id,
        "frozen-before-rebuild",
    )
    with duckdb.connect(str(store.database_path)) as connection:
        rebuild_derived_schema(connection, SCHEMA_VERSION)
    snapshot = store.list_snapshots()[0]
    dependencies = store.snapshot_dependencies(snapshot.snapshot_id)
    assert dependencies.evaluation_packet_ids == tuple(
        sorted(packet.routing.packet_id for packet in exports)
    )
    assert len(dependencies.audit_protocol_ids) == 1
    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(snapshot.snapshot_id)

    result = store.prune_snapshot(snapshot.snapshot_id, force=True)

    assert result.dependent_evaluation_packet_ids == dependencies.evaluation_packet_ids
    assert store.table_count("evaluation_packets") == 0
    assert store.table_count("evaluation_corpora") == 0
    assert store.table_count("audit_protocols") == 0


def test_snapshot_prune_rejects_partial_frozen_cohort_even_with_force(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    extra_source = SessionSource(
        source_id="extra-source",
        agent_name=AgentName.CODEX,
        source_path="/sessions/extra.jsonl",
    )
    captured = store.capture_source(extra_source, b"{}\n")
    extra_bundle = store.create_single_source_bundle(extra_source, captured, "extra-native")
    with duckdb.connect(str(store.database_path)) as connection:
        connection.execute(
            "UPDATE evaluation_packets SET snapshot_bundle_id = ? WHERE packet_id = ?",
            [extra_bundle.snapshot_bundle_id, exports[-1].routing.packet_id],
        )
    freeze_audit_protocol(
        store.database_path,
        exports[0].routing.evaluation_corpus_id,
        "partial-cohort-seed",
    )
    original_snapshot = next(row for row in store.list_snapshots() if row.source_id == "source-1")
    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(original_snapshot.snapshot_id, force=True)


def test_evaluation_cli_exports_and_imports_without_episode_generation(tmp_path) -> None:
    store, exports, normalization_run_id = evaluation_fixture(tmp_path)
    output = tmp_path / "packets"
    exported = runner.invoke(
        app,
        [
            "evaluation",
            "export-boundaries",
            normalization_run_id,
            "--output",
            str(output),
            "--db",
            str(store.database_path),
        ],
    )
    assert exported.exit_code == 0
    assert len(list(output.glob("*.judge.json"))) == len(exports)
    assert not list(output.glob("*.routing.json"))
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    judge = annotation(
        packet.routing.packet_id,
        packet.judge_packet.left_user_event_id,
        40,
        "split",
    )
    judge_path = tmp_path / "judge.json"
    judge_path.write_text(judge.model_dump_json())
    imported = runner.invoke(
        app,
        [
            "evaluation",
            "import-judge",
            "--input",
            str(judge_path),
            "--db",
            str(store.database_path),
        ],
    )
    assert imported.exit_code == 0
    pilot_output = tmp_path / "pilot-packets"
    pilot_exported = runner.invoke(
        app,
        [
            "evaluation",
            "export-pilot",
            "--output",
            str(pilot_output),
            "--db",
            str(store.database_path),
        ],
    )
    assert pilot_exported.exit_code == 0
    assert len(list(pilot_output.glob("*.judge.json"))) == 24
    capture_counts = {
        table: store.table_count(table)
        for table in ("source_blobs", "source_snapshots", "snapshot_bundles")
    }
    with duckdb.connect(str(store.database_path)) as connection:
        original_pilot_row = connection.execute(
            "SELECT DISTINCT snapshot_bundle_id FROM evaluation_packets "
            "WHERE evaluation_corpus_id = 'boundary-pilot-v1'"
        ).fetchone()
        assert original_pilot_row is not None
        original_pilot_bundle = original_pilot_row[0]
    repeated_pilot = runner.invoke(
        app,
        [
            "evaluation",
            "export-pilot",
            "--output",
            str(tmp_path / "pilot-packets-repeat"),
            "--db",
            str(store.database_path),
        ],
    )
    assert repeated_pilot.exit_code == 0
    assert {table: store.table_count(table) for table in capture_counts} == capture_counts
    with duckdb.connect(str(store.database_path)) as connection:
        repeated_pilot_row = connection.execute(
            "SELECT DISTINCT snapshot_bundle_id FROM evaluation_packets "
            "WHERE evaluation_corpus_id = 'boundary-pilot-v1'"
        ).fetchone()
        assert repeated_pilot_row is not None
        assert repeated_pilot_row[0] == original_pilot_bundle
    pilot_protocol = freeze_audit_protocol(
        store.database_path,
        "boundary-pilot-v1",
        "pilot-seed-v1",
    )
    assert len(pilot_protocol.cohort_packet_ids) == 24
    assert not pilot_protocol.eligible_packet_ids
    assert not pilot_protocol.selected_packet_ids
    unavailable = runner.invoke(app, ["evaluation", "export-episodes"])
    assert unavailable.exit_code == 1
    assert "unavailable" in unavailable.stdout
