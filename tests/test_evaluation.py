from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
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
from session_doctor.evaluation_packets import canonical_json, export_boundary_packets
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
)
from session_doctor.store import (
    DuckDBStore,
    EvaluationImportError,
    SnapshotPruneBlocked,
    create_reference_resolution,
    import_human_adjudication,
    import_judge_annotation,
    register_evaluation_packet,
    resolve_judge_panel,
    select_panel_audit,
)
from session_doctor.store.evaluation import audit_bucket

runner = CliRunner()


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
        for index in range(5)
    ]
    roles = [
        NormalizedRole.USER,
        NormalizedRole.ASSISTANT,
        NormalizedRole.USER,
        NormalizedRole.ASSISTANT,
        NormalizedRole.USER,
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
    for packet_export in exports:
        register_evaluation_packet(
            store.database_path,
            coverage.current_normalization_run_id,
            packet_export,
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
    assert len(exports) == 2
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
    assert first.judge_packet.left_user_event_id == "event-0"
    assert first.judge_packet.right_user_event_id == "event-2"
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


def test_judge_import_rejects_hallucinated_evidence_and_target_judge(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    evidence_id = packet.judge_packet.left_user_event_id
    valid = annotation(packet.routing.packet_id, evidence_id, 1, "split")
    assert import_judge_annotation(store.database_path, valid) == valid

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
    wrong_protocol = valid.model_copy(
        update={
            "judge_annotation_id": "wrong-protocol",
            "annotation_protocol_version": "other-protocol",
        }
    )
    with pytest.raises(EvaluationImportError, match="protocol"):
        import_judge_annotation(store.database_path, wrong_protocol)


def test_panel_consensus_audit_and_reference_records_remain_separate(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    packet = exports[0]
    assert isinstance(packet.judge_packet, BoundaryPacket)
    evidence_id = packet.judge_packet.left_user_event_id
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
    seed = next(
        f"seed-{index}"
        for index in range(100)
        if audit_bucket(f"seed-{index}", packet.routing.packet_id) >= 20
    )
    audit = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
        seed,
        eligible=True,
        selected_at=datetime(2026, 7, 13, 13, 1, tzinfo=UTC),
    )
    repeated = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
        seed,
        eligible=True,
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
    seed = next(
        f"selected-{index}"
        for index in range(100)
        if audit_bucket(f"selected-{index}", packet.routing.packet_id) < 20
    )
    audit = select_panel_audit(
        store.database_path,
        panel.judge_panel_resolution_id,
        seed,
        eligible=True,
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
        "forced-eligible-seed",
        eligible=True,
    )
    assert audit.eligibility_status == "ineligible"
    assert audit.selection_status is AuditSelectionStatus.NOT_SELECTED


def test_pilot_manifest_has_stratified_preseal_cases() -> None:
    manifest = json.loads(Path("evaluation/boundary-pilot-v1.json").read_text())
    cases = manifest["cases"]
    assert 20 <= len(cases) <= 30
    assert {row["adapter"] for row in cases} == {"claude", "codex", "pi"}
    assert all(row["source_family_status"] in {"unknown", "ambiguous"} for row in cases)
    assert manifest["episode_packet_generation"].startswith("blocked")
    strata = {value for row in cases for value in row["strata"]}
    assert {"active", "blocker", "incomplete", "prior_disagreement", "success"} <= strata


def test_snapshot_prune_reports_and_removes_evaluation_dependencies(tmp_path) -> None:
    store, exports, _ = evaluation_fixture(tmp_path)
    snapshot = store.list_snapshots()[0]
    dependencies = store.snapshot_dependencies(snapshot.snapshot_id)
    assert dependencies.evaluation_packet_ids == tuple(
        sorted(packet.routing.packet_id for packet in exports)
    )
    with pytest.raises(SnapshotPruneBlocked):
        store.prune_snapshot(snapshot.snapshot_id)

    result = store.prune_snapshot(snapshot.snapshot_id, force=True)

    assert result.dependent_evaluation_packet_ids == dependencies.evaluation_packet_ids
    assert store.table_count("evaluation_packets") == 0


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
    unavailable = runner.invoke(app, ["evaluation", "export-episodes"])
    assert unavailable.exit_code == 1
    assert "unavailable" in unavailable.stdout
