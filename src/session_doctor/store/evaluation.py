from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from session_doctor.evaluation_models import (
    AuditEligibilityStatus,
    AuditSelection,
    AuditSelectionStatus,
    EvaluationPacketExport,
    HumanAdjudication,
    HumanReviewKind,
    JudgeAnnotation,
    JudgeConsensusStatus,
    JudgePanelResolution,
    ReferenceResolution,
    ReferenceResolutionStatus,
    RoutingEnvelope,
)
from session_doctor.evaluation_packets import canonical_json
from session_doctor.ids import stable_id

from .connection import transaction, write_connection
from .json_values import duckdb_value, parse_string_list


class EvaluationImportError(ValueError):
    pass


def register_evaluation_packet(
    database_path: Path,
    normalization_run_id: str,
    packet_export: EvaluationPacketExport,
) -> None:
    routing = packet_export.routing
    judge_packet = packet_export.judge_packet
    judge_json = canonical_json(judge_packet.model_dump(mode="json"))
    judge_hash = hashlib.sha256(judge_json.encode()).hexdigest()
    if routing.packet_id != judge_packet.packet_id or routing.judge_packet_hash != judge_hash:
        raise EvaluationImportError("packet routing hash or identity mismatch")
    evidence_ids = sorted(packet_evidence_ids(judge_packet.model_dump(mode="json")))
    values = (
        routing.packet_id,
        routing.schema_version,
        routing.annotation_protocol_version,
        routing.packet_kind.value,
        normalization_run_id,
        canonical_json(routing.model_dump(mode="json")),
        judge_json,
        judge_hash,
        canonical_json(evidence_ids),
        canonical_json(judge_packet.allowed_answers),
    )
    with write_connection(database_path) as connection, transaction(connection):
        normalization_exists = connection.execute(
            "SELECT 1 FROM normalization_runs WHERE normalization_run_id = ?",
            [normalization_run_id],
        ).fetchone()
        if normalization_exists is None:
            raise EvaluationImportError("normalization run not found")
        insert_immutable(
            connection,
            "evaluation_packets",
            "packet_id",
            (
                "packet_id",
                "schema_version",
                "annotation_protocol_version",
                "packet_kind",
                "normalization_run_id",
                "routing_json",
                "judge_packet_json",
                "judge_packet_hash",
                "evidence_ids_json",
                "allowed_answers_json",
            ),
            values,
        )


def import_judge_annotation(database_path: Path, annotation: JudgeAnnotation) -> JudgeAnnotation:
    with write_connection(database_path) as connection, transaction(connection):
        packet = packet_row(connection, annotation.packet_id)
        validate_protocol(annotation, packet)
        allowed_answers = set(parse_string_list(packet[5]))
        evidence_ids = set(parse_string_list(packet[4]))
        if annotation.answer not in allowed_answers:
            raise EvaluationImportError("judge answer is outside packet rubric")
        if not set(annotation.evidence_ids).issubset(evidence_ids):
            raise EvaluationImportError("judge cited evidence outside packet")
        routing = RoutingEnvelope.model_validate_json(str(packet[3]))
        if judge_is_excluded(
            annotation.judge_provider,
            annotation.judge_model,
            routing.excluded_judge_identities,
        ):
            raise EvaluationImportError("target model cannot judge its own packet")
        values = (
            annotation.judge_annotation_id,
            annotation.schema_version,
            annotation.annotation_protocol_version,
            annotation.packet_id,
            annotation.judge_model,
            annotation.judge_provider,
            annotation.judge_prompt_version,
            annotation.answer,
            canonical_json(annotation.evidence_ids),
            annotation.rationale,
            annotation.created_at,
        )
        insert_immutable(
            connection,
            "judge_annotations",
            "judge_annotation_id",
            (
                "judge_annotation_id",
                "schema_version",
                "annotation_protocol_version",
                "packet_id",
                "judge_model",
                "judge_provider",
                "judge_prompt_version",
                "answer",
                "evidence_ids_json",
                "rationale",
                "created_at",
            ),
            values,
        )
    return annotation


def resolve_judge_panel(
    database_path: Path,
    packet_id: str,
    judge_annotation_ids: tuple[str, ...],
    *,
    resolved_at: datetime | None = None,
) -> JudgePanelResolution:
    annotation_ids = tuple(sorted(set(judge_annotation_ids)))
    if len(annotation_ids) != len(judge_annotation_ids):
        raise EvaluationImportError("panel annotation IDs must be distinct")
    if not annotation_ids or len(annotation_ids) > 3:
        raise EvaluationImportError("panel requires one to three annotations")
    with write_connection(database_path) as connection, transaction(connection):
        packet = packet_row(connection, packet_id)
        placeholders = ", ".join("?" for _ in annotation_ids)
        rows = connection.execute(
            f"""
            SELECT judge_annotation_id, packet_id, annotation_protocol_version,
                judge_provider, judge_model, answer
            FROM judge_annotations WHERE judge_annotation_id IN ({placeholders})
            ORDER BY judge_annotation_id
            """,
            list(annotation_ids),
        ).fetchall()
        if len(rows) != len(annotation_ids):
            raise EvaluationImportError("panel annotation not found")
        if any(row[1] != packet_id or row[2] != packet[1] for row in rows):
            raise EvaluationImportError("cross-packet or cross-protocol panel")
        judge_identities = {(str(row[3]), str(row[4])) for row in rows}
        if len(judge_identities) != len(rows):
            raise EvaluationImportError("panel judges must be distinct")
        answers = {str(row[5]) for row in rows}
        status = (
            JudgeConsensusStatus.INSUFFICIENT
            if len(rows) < 3
            else JudgeConsensusStatus.UNANIMOUS
            if len(answers) == 1
            else JudgeConsensusStatus.DISPUTED
        )
        unanimous_answer = next(iter(answers)) if status is JudgeConsensusStatus.UNANIMOUS else None
        resolution = JudgePanelResolution(
            judge_panel_resolution_id=stable_id("judge-panel", packet_id, *annotation_ids),
            schema_version=str(packet[0]),
            annotation_protocol_version=str(packet[1]),
            packet_id=packet_id,
            judge_annotation_ids=list(annotation_ids),
            consensus_status=status,
            unanimous_answer=unanimous_answer,
            resolved_at=resolved_at or datetime.now(UTC),
        )
        insert_immutable_model(
            connection,
            "judge_panel_resolutions",
            "judge_panel_resolution_id",
            resolution,
            json_fields={"judge_annotation_ids": "judge_annotation_ids_json"},
        )
    return resolution


def select_panel_audit(
    database_path: Path,
    judge_panel_resolution_id: str,
    selection_seed_id: str,
    *,
    eligible: bool,
    selected_at: datetime | None = None,
) -> AuditSelection:
    with write_connection(database_path) as connection, transaction(connection):
        panel = panel_row(connection, judge_panel_resolution_id)
        if panel[3] != JudgeConsensusStatus.UNANIMOUS.value:
            raise EvaluationImportError("only unanimous panels receive audit selection")
        packet = packet_row(connection, str(panel[2]))
        routing = RoutingEnvelope.model_validate_json(str(packet[3]))
        actual_eligible = eligible and routing.identity_exposure_status.value == "blind_eligible"
        is_selected = actual_eligible and audit_bucket(selection_seed_id, str(panel[2])) < 20
        selection = AuditSelection(
            audit_selection_id=stable_id(
                "audit-selection", judge_panel_resolution_id, selection_seed_id
            ),
            schema_version=str(panel[0]),
            annotation_protocol_version=str(panel[1]),
            packet_id=str(panel[2]),
            judge_panel_resolution_id=judge_panel_resolution_id,
            eligibility_status=(
                AuditEligibilityStatus.ELIGIBLE
                if actual_eligible
                else AuditEligibilityStatus.INELIGIBLE
            ),
            selection_status=(
                AuditSelectionStatus.SELECTED if is_selected else AuditSelectionStatus.NOT_SELECTED
            ),
            selection_seed_id=selection_seed_id,
            selection_reason=(
                "frozen_twenty_percent_sample"
                if actual_eligible
                else "packet_ineligible_for_blinded_audit"
            ),
            selected_at=selected_at or datetime.now(UTC),
        )
        insert_immutable_model(
            connection,
            "audit_selections",
            "audit_selection_id",
            selection,
        )
    return selection


def import_human_adjudication(
    database_path: Path, adjudication: HumanAdjudication
) -> HumanAdjudication:
    with write_connection(database_path) as connection, transaction(connection):
        panel = panel_row(connection, adjudication.judge_panel_resolution_id)
        if (
            adjudication.packet_id != panel[2]
            or adjudication.annotation_protocol_version != panel[1]
        ):
            raise EvaluationImportError("cross-packet or cross-protocol adjudication")
        packet = packet_row(connection, adjudication.packet_id)
        if adjudication.answer not in set(parse_string_list(packet[5])):
            raise EvaluationImportError("human answer is outside packet rubric")
        if not set(adjudication.evidence_ids).issubset(set(parse_string_list(packet[4]))):
            raise EvaluationImportError("human cited evidence outside packet")
        validate_human_review(connection, adjudication, str(panel[3]))
        insert_immutable_model(
            connection,
            "human_adjudications",
            "human_adjudication_id",
            adjudication,
            json_fields={"evidence_ids": "evidence_ids_json"},
        )
    return adjudication


def create_reference_resolution(
    database_path: Path,
    judge_panel_resolution_id: str,
    *,
    resolved_at: datetime | None = None,
) -> ReferenceResolution:
    with write_connection(database_path) as connection, transaction(connection):
        panel = panel_row(connection, judge_panel_resolution_id)
        audit = connection.execute(
            "SELECT * FROM audit_selections WHERE judge_panel_resolution_id = ?",
            [judge_panel_resolution_id],
        ).fetchone()
        human_rows = connection.execute(
            "SELECT * FROM human_adjudications WHERE judge_panel_resolution_id = ? "
            "ORDER BY human_adjudication_id",
            [judge_panel_resolution_id],
        ).fetchall()
        status = str(panel[3])
        human = human_rows[0] if len(human_rows) == 1 else None
        if len(human_rows) > 1:
            raise EvaluationImportError("panel has multiple human adjudications")
        if status == JudgeConsensusStatus.UNANIMOUS.value:
            if audit is None:
                raise EvaluationImportError("unanimous panel requires audit selection")
            if audit[6] == AuditSelectionStatus.NOT_SELECTED.value and human is None:
                resolution_status = ReferenceResolutionStatus.JUDGE_CONSENSUS
                answer = str(panel[4])
            elif audit[6] == AuditSelectionStatus.SELECTED.value and human is not None:
                answer = str(human[8])
                resolution_status = human_resolution_status(answer)
            else:
                raise EvaluationImportError("selected audit requires human adjudication")
        else:
            if audit is not None:
                raise EvaluationImportError("non-unanimous panel cannot have audit selection")
            if human is None:
                raise EvaluationImportError("disputed or insufficient panel requires human review")
            answer = str(human[8])
            resolution_status = human_resolution_status(answer)
        resolution = ReferenceResolution(
            reference_resolution_id=stable_id(
                "reference-resolution",
                judge_panel_resolution_id,
                audit[0] if audit else None,
                human[0] if human else None,
            ),
            schema_version=str(panel[0]),
            annotation_protocol_version=str(panel[1]),
            packet_id=str(panel[2]),
            resolution_status=resolution_status,
            answer=answer,
            source_judge_panel_resolution_id=judge_panel_resolution_id,
            source_audit_selection_id=str(audit[0]) if audit else None,
            source_human_adjudication_id=str(human[0]) if human else None,
            resolved_at=resolved_at or datetime.now(UTC),
        )
        insert_immutable_model(
            connection,
            "reference_resolutions",
            "reference_resolution_id",
            resolution,
        )
    return resolution


def validate_human_review(
    connection: duckdb.DuckDBPyConnection,
    adjudication: HumanAdjudication,
    panel_status: str,
) -> None:
    expected_kind = {
        JudgeConsensusStatus.DISPUTED.value: HumanReviewKind.PANEL_DISPUTE,
        JudgeConsensusStatus.INSUFFICIENT.value: HumanReviewKind.PANEL_INSUFFICIENT,
        JudgeConsensusStatus.UNANIMOUS.value: HumanReviewKind.CONSENSUS_AUDIT,
    }[panel_status]
    if adjudication.review_kind is not expected_kind:
        raise EvaluationImportError("human review kind is incompatible with panel")
    if expected_kind is HumanReviewKind.CONSENSUS_AUDIT:
        if adjudication.audit_selection_id is None:
            raise EvaluationImportError("consensus audit requires audit selection")
        audit = connection.execute(
            "SELECT packet_id, judge_panel_resolution_id, selection_status "
            "FROM audit_selections WHERE audit_selection_id = ?",
            [adjudication.audit_selection_id],
        ).fetchone()
        if audit != (
            adjudication.packet_id,
            adjudication.judge_panel_resolution_id,
            AuditSelectionStatus.SELECTED.value,
        ):
            raise EvaluationImportError("audit selection is incompatible")
    elif adjudication.audit_selection_id is not None:
        raise EvaluationImportError("panel dispute review cannot cite audit selection")


def packet_row(connection: duckdb.DuckDBPyConnection, packet_id: str) -> tuple:
    row = connection.execute(
        """
        SELECT schema_version, annotation_protocol_version, packet_kind,
            routing_json, evidence_ids_json, allowed_answers_json
        FROM evaluation_packets WHERE packet_id = ?
        """,
        [packet_id],
    ).fetchone()
    if row is None:
        raise EvaluationImportError("packet not found")
    return row


def panel_row(connection: duckdb.DuckDBPyConnection, panel_id: str) -> tuple:
    row = connection.execute(
        """
        SELECT schema_version, annotation_protocol_version, packet_id,
            consensus_status, unanimous_answer
        FROM judge_panel_resolutions WHERE judge_panel_resolution_id = ?
        """,
        [panel_id],
    ).fetchone()
    if row is None:
        raise EvaluationImportError("panel resolution not found")
    return row


def validate_protocol(annotation: JudgeAnnotation, packet: tuple) -> None:
    if annotation.schema_version != packet[0]:
        raise EvaluationImportError("judge schema version mismatch")
    if annotation.annotation_protocol_version != packet[1]:
        raise EvaluationImportError("judge annotation protocol mismatch")


def judge_is_excluded(provider: str, model: str, excluded_identities: list[str]) -> bool:
    for identity in excluded_identities:
        try:
            parsed = json.loads(identity)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("model") == model and parsed.get("provider") in {provider, None}:
            return True
    return False


def packet_evidence_ids(value: object) -> set[str]:
    evidence: set[str] = set()
    if isinstance(value, dict):
        for key, row in value.items():
            if key in {
                "evidence_id",
                "source_event_id",
                "left_user_event_id",
                "right_user_event_id",
            } and isinstance(row, str):
                evidence.add(row)
            evidence.update(packet_evidence_ids(row))
    elif isinstance(value, list):
        for row in value:
            evidence.update(packet_evidence_ids(row))
    return evidence


def audit_bucket(seed_id: str, packet_id: str) -> int:
    digest = hashlib.sha256(f"{seed_id}\0{packet_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def human_resolution_status(answer: str) -> ReferenceResolutionStatus:
    return (
        ReferenceResolutionStatus.AMBIGUOUS
        if answer == "ambiguous"
        else ReferenceResolutionStatus.HUMAN_RESOLVED
    )


def insert_immutable_model(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    id_column: str,
    model,
    *,
    json_fields: dict[str, str] | None = None,
) -> None:
    payload = model.model_dump(mode="python")
    for source_field, target_column in (json_fields or {}).items():
        payload[target_column] = canonical_json(payload.pop(source_field))
    for key, value in tuple(payload.items()):
        if hasattr(value, "value"):
            payload[key] = value.value
    insert_immutable(
        connection,
        table,
        id_column,
        tuple(payload),
        tuple(payload.values()),
    )


def insert_immutable(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    id_column: str,
    columns: tuple[str, ...],
    values: tuple[object, ...],
) -> None:
    stored_values = tuple(duckdb_value(value) for value in values)
    placeholders = ", ".join("?" for _ in stored_values)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT DO NOTHING",
        list(stored_values),
    )
    identifier = stored_values[columns.index(id_column)]
    stored = connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {id_column} = ?",
        [identifier],
    ).fetchone()
    if stored != stored_values:
        raise EvaluationImportError(f"immutable {table} record conflict")
