from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from fractions import Fraction
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from session_doctor.evaluation_models import (
    ANNOTATION_PROTOCOL_VERSION,
    EVALUATION_SCHEMA_VERSION,
    BoundaryPacket,
    EvaluationPacketExport,
    HumanAdjudication,
    IdentityExposureStatus,
    JudgeAnnotation,
    JudgePanelResolution,
    PacketEvent,
    PacketKind,
    RoutingEnvelope,
)
from session_doctor.ids import stable_id
from session_doctor.schemas import NormalizedRole, SemanticFoundation

if TYPE_CHECKING:
    from session_doctor.store.normalization_runs import StoredNormalization

BOUNDARY_CAPABILITIES = {"delegation_topology", "native_causal_links"}


def export_boundary_packets(
    stored: StoredNormalization,
    foundation: SemanticFoundation,
    *,
    evaluation_corpus_id: str | None = None,
) -> tuple[EvaluationPacketExport, ...]:
    evaluation_corpus_id = evaluation_corpus_id or (
        f"boundary-development:{stored.run.normalization_run_id}"
    )
    events = packet_events(stored)
    user_positions = [
        index for index, event in enumerate(events) if event.role == NormalizedRole.USER.value
    ]
    exports: list[EvaluationPacketExport] = []
    for left_position, right_position in zip(user_positions, user_positions[1:], strict=False):
        exports.append(
            boundary_packet(
                stored,
                foundation,
                events,
                left_position,
                right_position,
                evaluation_corpus_id,
            )
        )
    return tuple(exports)


def boundary_packet(
    stored: StoredNormalization,
    foundation: SemanticFoundation,
    events: list[PacketEvent],
    left_position: int,
    right_position: int,
    evaluation_corpus_id: str,
) -> EvaluationPacketExport:
    left = events[left_position]
    right = events[right_position]
    packet_seed = digest_json(
        {
            "kind": PacketKind.BOUNDARY,
            "normalization_run_id": stored.run.normalization_run_id,
            "left": left.source_event_id or left.evidence_id,
            "right": right.source_event_id or right.evidence_id,
            "schema": EVALUATION_SCHEMA_VERSION,
            "protocol": ANNOTATION_PROTOCOL_VERSION,
            "evaluation_corpus_id": evaluation_corpus_id,
        }
    )
    targets, redaction_terms = routing_identities(stored, foundation)
    blinded_left = blind_event(left, redaction_terms, packet_seed)
    blinded_right = blind_event(right, redaction_terms, packet_seed)
    packet = BoundaryPacket(
        packet_id="",
        left_user_event_id=blinded_left.source_event_id or blinded_left.evidence_id,
        right_user_event_id=blinded_right.source_event_id or blinded_right.evidence_id,
        adjacent_user_turns=[
            blinded_left,
            blinded_right,
        ],
        intervening_normalized_events=[
            blind_event(event, redaction_terms, packet_seed)
            for event in events[left_position + 1 : right_position]
        ],
        bounded_context_events=[
            blind_event(events[index], redaction_terms, packet_seed)
            for index in (left_position - 1, right_position + 1)
            if 0 <= index < len(events)
        ],
        anonymized_capability_support=[
            {
                "capability": row.capability,
                "support": row.support.value,
                "evidence_status": row.evidence_status.value,
            }
            for row in foundation.capabilities
            if row.capability in BOUNDARY_CAPABILITIES
        ],
    )
    packet_id = digest_json(
        {
            "normalization_run_id": stored.run.normalization_run_id,
            "evaluation_corpus_id": evaluation_corpus_id,
            "judge_packet": packet.model_dump(mode="json"),
        }
    )
    packet = packet.model_copy(update={"packet_id": packet_id})
    judge_json = canonical_json(packet.model_dump(mode="json"))
    exposure = identity_exposure_status(judge_json, targets)
    routing = RoutingEnvelope(
        packet_id=packet_id,
        packet_kind=PacketKind.BOUNDARY,
        evaluation_corpus_id=evaluation_corpus_id,
        normalization_run_id=stored.run.normalization_run_id,
        snapshot_bundle_id=stored.run.snapshot_bundle_id,
        target_model_identities=targets,
        excluded_judge_identities=targets,
        identity_exposure_status=exposure,
        judge_packet_hash=hashlib.sha256(judge_json.encode()).hexdigest(),
    )
    return EvaluationPacketExport(routing=routing, judge_packet=packet)


def packet_events(stored: StoredNormalization) -> list[PacketEvent]:
    bundle = stored.bundle
    record_indexes = {event.event_id: event.record_index for event in bundle.raw_events}
    message_order = {
        message.source_event_id: Fraction(index)
        for index, message in enumerate(bundle.messages)
        if message.source_event_id in record_indexes
    }

    def anchored_order(source_event_id: str | None) -> Fraction | None:
        return message_order.get(source_event_id) if source_event_id is not None else None

    rows: list[tuple[Fraction, int, int, str, PacketEvent]] = []

    def add(
        evidence_id: str,
        entity_kind: str,
        source_event_id: str | None,
        entity_rank: int,
        entity_order: int,
        normalized_order: Fraction | None = None,
        *,
        role: str | None = None,
        text: str | None = None,
        text_hash: str | None = None,
        text_length: int | None = None,
        structure: dict[str, object] | None = None,
    ) -> None:
        resolved_source_event_id = source_event_id if source_event_id in record_indexes else None
        rows.append(
            (
                normalized_order
                if normalized_order is not None
                else Fraction(record_indexes[resolved_source_event_id])
                if resolved_source_event_id is not None
                else Fraction(2**31 - 1),
                entity_rank,
                entity_order,
                evidence_id,
                PacketEvent(
                    evidence_id=evidence_id,
                    source_event_id=resolved_source_event_id,
                    entity_kind=entity_kind,
                    role=role,
                    text=text,
                    text_hash=text_hash,
                    text_length=text_length,
                    structure=structure or {},
                ),
            )
        )

    for index, message in enumerate(bundle.messages):
        add(
            message.message_id,
            "message",
            message.source_event_id,
            0,
            index,
            normalized_order=Fraction(index),
            role=message.role.value,
            text=message.text,
            text_hash=message.text_hash,
            text_length=message.text_length,
            structure={"content_block_types": message.content_block_types},
        )
    for index, call in enumerate(bundle.tool_calls):
        add(
            call.tool_call_id,
            "tool_call",
            call.source_event_id,
            1,
            index,
            normalized_order=anchored_order(call.source_event_id),
            structure={"name": call.name},
        )
    for index, result in enumerate(bundle.tool_results):
        add(
            result.tool_result_id,
            "tool_result",
            result.source_event_id,
            2,
            index,
            normalized_order=anchored_order(result.source_event_id),
            structure={"is_error": result.is_error, "output_length": result.output_length},
        )
    for index, command in enumerate(bundle.command_runs):
        add(
            command.command_run_id,
            "command_run",
            command.source_event_id,
            3,
            index,
            normalized_order=anchored_order(command.source_event_id),
            structure={"exit_code": command.exit_code, "output_length": command.output_length},
        )
    for index, activity in enumerate(bundle.file_activities):
        add(
            activity.file_activity_id,
            "file_activity",
            activity.source_event_id,
            4,
            index,
            normalized_order=anchored_order(activity.source_event_id),
            structure={"operation": activity.operation},
        )
    return [row[4] for row in sorted(rows, key=lambda row: row[:4])]


def routing_identities(
    stored: StoredNormalization,
    foundation: SemanticFoundation,
) -> tuple[list[str], tuple[str, ...]]:
    targets = sorted(
        {
            canonical_json(model.model_dump(mode="json"))
            for model in foundation.model_identity.models
        }
    )
    terms: set[str] = {stored.run.adapter_name}
    for model in foundation.model_identity.models:
        terms.add(model.model)
        if model.provider:
            terms.add(model.provider)
    redaction_terms: list[str] = [term for term in terms if term]
    return targets, tuple(sorted(redaction_terms, key=lambda term: len(term), reverse=True))


def blind_event(
    event: PacketEvent,
    redaction_terms: tuple[str, ...],
    packet_seed: str,
) -> PacketEvent:
    text = event.text
    if text is not None:
        for term in redaction_terms:
            text = replace_case_insensitive(text, term, "[identity_redacted]")
    structure = redact_value(event.structure, redaction_terms)
    redacted_hash = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
    return event.model_copy(
        update={
            "evidence_id": opaque_evidence_id(packet_seed, event.evidence_id),
            "source_event_id": (
                opaque_source_event_id(packet_seed, event)
                if event.source_event_id is not None
                else None
            ),
            "text": text,
            "text_hash": redacted_hash,
            "text_length": len(text) if text is not None else None,
            "structure": structure,
        }
    )


def opaque_evidence_id(packet_seed: str, evidence_id: str) -> str:
    return "ev_" + hashlib.sha256(f"{packet_seed}\0{evidence_id}".encode()).hexdigest()[:24]


def opaque_source_event_id(packet_seed: str, event: PacketEvent) -> str:
    return opaque_evidence_id(
        packet_seed,
        f"source:{event.source_event_id or event.evidence_id}:{event.evidence_id}",
    )


def redact_value(value: object, terms: tuple[str, ...]) -> object:
    if isinstance(value, str):
        redacted = value
        for term in terms:
            redacted = replace_case_insensitive(redacted, term, "[identity_redacted]")
        return redacted
    if isinstance(value, list):
        return [redact_value(row, terms) for row in value]
    if isinstance(value, dict):
        return {str(key): redact_value(row, terms) for key, row in value.items()}
    return value


def replace_case_insensitive(value: str, target: str, replacement: str) -> str:
    if len(target) < 3:
        return re.sub(
            rf"(?<!\w){re.escape(target)}(?!\w)",
            replacement,
            value,
            flags=re.IGNORECASE,
        )
    lower_value = value.casefold()
    lower_target = target.casefold()
    output: list[str] = []
    cursor = 0
    while (index := lower_value.find(lower_target, cursor)) >= 0:
        output.append(value[cursor:index])
        output.append(replacement)
        cursor = index + len(target)
    output.append(value[cursor:])
    return "".join(output)


def identity_exposure_status(
    judge_packet_json: str,
    target_identities: list[str],
) -> IdentityExposureStatus:
    if not target_identities:
        return IdentityExposureStatus.TARGET_IDENTITY_UNVERIFIABLE
    lowered = judge_packet_json.casefold()
    target_parts = [
        part
        for identity in target_identities
        for part in json.loads(identity).values()
        if isinstance(part, str)
    ]
    exposed_parts = [part for part in target_parts if len(part) < 3 or part.casefold() in lowered]
    return (
        IdentityExposureStatus.IDENTITY_EXPOSED
        if exposed_parts
        else IdentityExposureStatus.BLIND_ELIGIBLE
    )


def write_packet_exports(
    exports: tuple[EvaluationPacketExport, ...], output_directory: Path
) -> None:
    temporary = stage_packet_exports(exports, output_directory)
    try:
        publish_staged_packet_exports(temporary, output_directory)
    except Exception:
        discard_staged_packet_exports(temporary)
        raise


def stage_packet_exports(
    exports: tuple[EvaluationPacketExport, ...], output_directory: Path
) -> Path:
    if output_directory.exists():
        raise ValueError("evaluation output directory already exists")
    if not output_directory.parent.is_dir():
        raise ValueError("evaluation output parent directory does not exist")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent)
    )
    try:
        for packet_export in exports:
            packet_id = packet_export.routing.packet_id
            (temporary / f"{packet_id}.judge.json").write_text(
                canonical_json(packet_export.judge_packet.model_dump(mode="json")) + "\n"
            )
        return temporary
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def publish_staged_packet_exports(temporary: Path, output_directory: Path) -> None:
    if output_directory.exists():
        raise ValueError("evaluation output directory already exists")
    os.replace(temporary, output_directory)


def discard_staged_packet_exports(temporary: Path) -> None:
    shutil.rmtree(temporary, ignore_errors=True)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def boundary_pilot_corpus_bytes() -> bytes:
    data_root = files("session_doctor.evaluation_data")
    manifest = json.loads(data_root.joinpath("boundary-pilot-v1.json").read_text())
    sources = json.loads(data_root.joinpath(str(manifest["source_corpus"])).read_text())
    if (
        manifest.get("manifest_version") != "boundary-pilot-v1"
        or manifest.get("annotation_protocol_version") != ANNOTATION_PROTOCOL_VERSION
        or len(manifest.get("cases", [])) != 24
        or sources.get("schema_version") != "boundary-pilot-sources-v1"
    ):
        raise ValueError("checked boundary pilot contract is invalid")
    return (canonical_json({"manifest": manifest, "sources": sources}) + "\n").encode()


def load_segmentation_calibration() -> dict[str, Any]:
    from session_doctor.segmentation import (
        CORRECTION_OR_CONTINUATION,
        EXPLICIT_NEW_TASK,
        broad_goal_similarity,
    )

    data_root = files("session_doctor.evaluation_data")
    document = json.loads(data_root.joinpath("segmentation-calibration-v1.json").read_text())
    required_versions = {
        "schema_version": "segmentation-calibration-v1",
        "annotation_protocol_version": ANNOTATION_PROTOCOL_VERSION,
        "boundary_reference_protocol_version": "boundary-reference-v1",
        "episode_evidence_input_version": "episode-evidence-input-v1",
        "segmentation_version": "segmentation-v2",
        "evaluation_corpus_id": "boundary-pilot-v1",
    }
    if any(document.get(key) != value for key, value in required_versions.items()):
        raise ValueError("segmentation calibration versions are invalid")
    corpus_bytes = boundary_pilot_corpus_bytes()
    if document.get("evaluation_corpus_sha256") != hashlib.sha256(corpus_bytes).hexdigest():
        raise ValueError("segmentation calibration corpus hash is invalid")
    if document.get("task_specific_packet_ids") != []:
        raise ValueError("segmentation calibration cannot assign task-specific packet IDs")

    corpus = json.loads(corpus_bytes)
    exports = export_boundary_pilot(corpus_bytes, "calibration-validation-bundle")
    packets = {
        row.routing.packet_id: row.judge_packet
        for row in exports
        if isinstance(row.judge_packet, BoundaryPacket)
    }
    if len(packets) != len(exports):
        raise ValueError("segmentation calibration contains a non-boundary packet")
    cases = {
        row.routing.packet_id: case
        for case, row in zip(corpus["manifest"]["cases"], exports, strict=True)
    }
    prompt_bytes = data_root.joinpath("boundary-calibration-prompt-v1.txt").read_bytes()
    if document.get("judge_prompt_sha256") != hashlib.sha256(prompt_bytes).hexdigest():
        raise ValueError("segmentation calibration judge prompt hash is invalid")
    ordered_packets = [packets[packet_id].model_dump(mode="json") for packet_id in sorted(packets)]
    judge_input = (
        prompt_bytes
        + b"\nPackets:\n"
        + (
            json.dumps(ordered_packets, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode()
    )
    if document.get("judge_input_sha256") != hashlib.sha256(judge_input).hexdigest():
        raise ValueError("segmentation calibration judge input hash is invalid")
    references = document.get("boundary_references")
    if not isinstance(references, list) or len(references) != len(packets):
        raise ValueError("segmentation calibration boundary references are incomplete")
    reference_packet_ids: set[str] = set()
    reference_ids: set[str] = set()
    judge_annotation_ids: set[str] = set()
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("segmentation calibration boundary reference is invalid")
        packet_id = reference.get("packet_id")
        answer = reference.get("answer")
        packet = packets.get(packet_id) if isinstance(packet_id, str) else None
        if not isinstance(packet, BoundaryPacket) or answer not in packet.allowed_answers:
            raise ValueError("segmentation calibration reference packet or answer is invalid")
        case = cases[packet_id]
        if (
            reference.get("pilot_case_id") != case["case_id"]
            or reference.get("source_id") != case["source_id"]
            or reference.get("source_region") != case["region"]
        ):
            raise ValueError("segmentation calibration reference source is invalid")
        expected_reference_id = digest_json(
            {
                "protocol": "boundary-reference-v1",
                "packet_id": packet_id,
                "answer": answer,
            }
        )
        reference_id = reference.get("boundary_reference_id")
        if reference_id != expected_reference_id:
            raise ValueError("segmentation calibration boundary reference ID is invalid")
        if (
            reference.get("left_user_event_id") != packet.left_user_event_id
            or reference.get("right_user_event_id") != packet.right_user_event_id
        ):
            raise ValueError("segmentation calibration boundary anchors are invalid")
        annotations = reference.get("judge_annotations")
        if not isinstance(annotations, list) or len(annotations) != 3:
            raise ValueError("segmentation calibration requires three judges per packet")
        parsed_annotations = [
            JudgeAnnotation.model_validate(annotation) for annotation in annotations
        ]
        judge_identities = {
            (annotation.judge_provider, annotation.judge_model) for annotation in parsed_annotations
        }
        if len(judge_identities) != 3:
            raise ValueError("segmentation calibration panel judges must be distinct")
        citable_ids = {
            value
            for event in (
                packet.adjacent_user_turns
                + packet.intervening_normalized_events
                + packet.bounded_context_events
            )
            for value in (event.evidence_id, event.source_event_id)
            if value is not None
        }
        for annotation in parsed_annotations:
            evidence_ids = annotation.evidence_ids
            expected_annotation_id = digest_json(
                {
                    "packet_id": packet_id,
                    "provider": annotation.judge_provider,
                    "model": annotation.judge_model,
                    "prompt": "boundary-calibration-prompt-v1",
                    "answer": annotation.answer,
                    "evidence_ids": evidence_ids,
                }
            )
            if (
                annotation.packet_id != packet_id
                or annotation.judge_prompt_version != "boundary-calibration-prompt-v1"
                or annotation.judge_annotation_id != expected_annotation_id
                or annotation.answer not in packet.allowed_answers
                or not set(evidence_ids) <= citable_ids
                or annotation.model_dump(mode="json")["created_at"] != document.get("frozen_at")
            ):
                raise ValueError("segmentation calibration judge provenance is invalid")
            judge_annotation_ids.add(annotation.judge_annotation_id)
        answers = {annotation.answer for annotation in parsed_annotations}
        panel_status = "unanimous" if len(answers) == 1 else "disputed"
        panel_answer = next(iter(answers)) if panel_status == "unanimous" else None
        if (
            reference.get("panel_status") != panel_status
            or reference.get("panel_answer") != panel_answer
        ):
            raise ValueError("segmentation calibration panel resolution is invalid")
        panel = JudgePanelResolution.model_validate(reference.get("judge_panel_resolution"))
        expected_panel_id = stable_id(
            "judge-panel",
            packet_id,
            *sorted(annotation.judge_annotation_id for annotation in parsed_annotations),
        )
        if (
            panel.judge_panel_resolution_id != expected_panel_id
            or panel.packet_id != packet_id
            or panel.judge_annotation_ids
            != sorted(annotation.judge_annotation_id for annotation in parsed_annotations)
            or panel.consensus_status.value != panel_status
            or panel.unanimous_answer != panel_answer
            or reference.get("source_judge_panel_resolution_id") != expected_panel_id
            or panel.model_dump(mode="json")["resolved_at"] != document.get("frozen_at")
        ):
            raise ValueError("segmentation calibration panel provenance is invalid")
        reference_packet_ids.add(packet_id)
        reference_ids.add(expected_reference_id)
    if reference_packet_ids != set(packets) or len(reference_ids) != len(references):
        raise ValueError("segmentation calibration references are duplicated or missing")
    if len(judge_annotation_ids) != len(references) * 3:
        raise ValueError("segmentation calibration judge annotations are duplicated")

    unanimous = sorted(
        reference["packet_id"]
        for reference in references
        if reference["panel_status"] == "unanimous"
    )
    seed = document.get("audit_selection_seed")
    if not isinstance(seed, str):
        raise ValueError("segmentation calibration audit seed is invalid")
    selected = set(
        sorted(
            unanimous,
            key=lambda packet_id: (
                hashlib.sha256(f"{seed}\0{packet_id}".encode()).hexdigest(),
                packet_id,
            ),
        )[: round(len(unanimous) * 0.2)]
    )
    development_audit_protocol_id = stable_id(
        "development-audit-protocol",
        ANNOTATION_PROTOCOL_VERSION,
        "boundary-pilot-v1",
        seed,
    )
    expected_audit_protocol = {
        "development_audit_protocol_id": development_audit_protocol_id,
        "annotation_protocol_version": ANNOTATION_PROTOCOL_VERSION,
        "evaluation_corpus_id": "boundary-pilot-v1",
        "selection_seed_id": seed,
        "cohort_packet_ids": sorted(packets),
        "unanimous_packet_ids": unanimous,
        "selected_packet_ids": sorted(selected),
        "frozen_at": document.get("frozen_at"),
        "claim_eligibility": "ineligible_target_identity_unverifiable",
    }
    if document.get("development_audit_protocol") != expected_audit_protocol:
        raise ValueError("segmentation calibration development audit protocol is invalid")
    candidate_rows: list[tuple[str, str]] = []
    for reference in references:
        packet_id = reference["packet_id"]
        expected_selection = "selected" if packet_id in selected else "not_selected"
        human_review = reference.get("human_review")
        panel_status = reference["panel_status"]
        if reference.get("audit_selection") != expected_selection:
            raise ValueError("segmentation calibration audit selection is invalid")
        if packet_id in selected:
            expected_review_kind = "consensus_audit"
        elif panel_status == "disputed":
            expected_review_kind = "panel_dispute"
        else:
            expected_review_kind = None
        if expected_review_kind is None:
            if (
                human_review is not None
                or reference.get("source_human_adjudication_id") is not None
                or reference.get("development_audit_selection_id") is not None
                or reference.get("development_audit_selection") is not None
            ):
                raise ValueError("segmentation calibration has an unexpected human review")
            resolved_answer = reference["panel_answer"]
            resolution_status = "judge_consensus"
        else:
            human = HumanAdjudication.model_validate(human_review)
            expected_audit_id = (
                stable_id("development-audit-selection", seed, packet_id)
                if packet_id in selected
                else None
            )
            expected_human_id = stable_id(
                "human-adjudication",
                reference["source_judge_panel_resolution_id"],
                human.reviewer_identity,
                human.answer,
                *human.evidence_ids,
            )
            expected_audit_selection = (
                {
                    "development_audit_selection_id": expected_audit_id,
                    "development_audit_protocol_id": development_audit_protocol_id,
                    "packet_id": packet_id,
                    "judge_panel_resolution_id": reference["source_judge_panel_resolution_id"],
                    "selection_seed_id": seed,
                    "selection_reason": "frozen_nearest_twenty_percent_development_sample",
                    "selected_at": document.get("frozen_at"),
                }
                if expected_audit_id is not None
                else None
            )
            if (
                human.review_kind.value != expected_review_kind
                or human.packet_id != packet_id
                or human.judge_panel_resolution_id != reference["source_judge_panel_resolution_id"]
                or human.audit_selection_id != expected_audit_id
                or human.human_adjudication_id != expected_human_id
                or human.answer not in packets[packet_id].allowed_answers
                or not set(human.evidence_ids)
                <= {
                    packets[packet_id].left_user_event_id,
                    packets[packet_id].right_user_event_id,
                }
                or reference.get("source_human_adjudication_id") != human.human_adjudication_id
                or reference.get("development_audit_selection_id") != expected_audit_id
                or reference.get("development_audit_selection") != expected_audit_selection
                or human.model_dump(mode="json")["reviewed_at"] != document.get("frozen_at")
            ):
                raise ValueError("segmentation calibration human review is invalid")
            resolved_answer = human.answer
            resolution_status = "ambiguous" if human.answer == "ambiguous" else "human_resolved"
        if (
            reference.get("answer") != resolved_answer
            or reference.get("resolution_status") != resolution_status
        ):
            raise ValueError("segmentation calibration reference resolution is invalid")
        packet = packets[packet_id]
        left_text = packet.adjacent_user_turns[0].text or ""
        right_text = packet.adjacent_user_turns[1].text or ""
        similarity = broad_goal_similarity(left_text, right_text)
        if EXPLICIT_NEW_TASK.search(right_text):
            candidate_decision = "split"
        elif CORRECTION_OR_CONTINUATION.search(right_text) or (
            similarity is not None and similarity >= 0.62
        ):
            candidate_decision = "no_split"
        else:
            candidate_decision = "ambiguous"
        if reference.get("candidate_decision") != candidate_decision:
            raise ValueError("segmentation calibration candidate decision is stale")
        candidate_rows.append((candidate_decision, resolved_answer))

    predicted_no_split = sum(candidate == "no_split" for candidate, _ in candidate_rows)
    true_no_split = sum(answer == "no_split" for _, answer in candidate_rows)
    matched_no_split = sum(
        candidate == answer == "no_split" for candidate, answer in candidate_rows
    )
    true_split = sum(answer == "split" for _, answer in candidate_rows)
    expected_candidate_metrics = {
        "exact_agreement": (
            f"{sum(candidate == answer for candidate, answer in candidate_rows)}/"
            f"{len(candidate_rows)}"
        ),
        "prediction_counts": {
            answer: sum(candidate == answer for candidate, _ in candidate_rows)
            for answer in ("split", "no_split", "ambiguous")
        },
        "no_split_precision": f"{matched_no_split}/{predicted_no_split}",
        "no_split_recall": f"{matched_no_split}/{true_no_split}",
        "split_precision": "unavailable_no_predictions",
        "split_recall": (
            f"{sum(candidate == answer == 'split' for candidate, answer in candidate_rows)}/"
            f"{true_split}"
            if true_split
            else "unavailable_no_references"
        ),
        "ambiguity_coverage": (
            f"{sum(candidate == 'ambiguous' for candidate, _ in candidate_rows)}/"
            f"{len(candidate_rows)}"
        ),
    }
    audited_consensus_errors = sum(
        reference["human_review"] is not None
        and reference["panel_status"] == "unanimous"
        and reference["human_review"]["answer"] != reference["panel_answer"]
        for reference in references
    )
    expected_metrics = {
        "packet_count": len(references),
        "judge_count_per_packet": 3,
        "unanimous_panel_count": len(unanimous),
        "disputed_panel_count": len(references) - len(unanimous),
        "audited_unanimous_count": len(selected),
        "audited_unanimous_rate": f"{len(selected)}/{len(unanimous)}",
        "audited_consensus_error_count": audited_consensus_errors,
        "reference_answer_counts": {
            answer: sum(reference["answer"] == answer for reference in references)
            for answer in ("split", "no_split", "ambiguous")
        },
        "candidate": expected_candidate_metrics,
    }
    if document.get("metrics") != expected_metrics:
        raise ValueError("segmentation calibration metrics are stale")

    episode_inputs = document.get("episode_evidence_inputs")
    if not isinstance(episode_inputs, list) or not episode_inputs:
        raise ValueError("segmentation calibration episode inputs are unavailable")
    input_ids: set[str] = set()
    for episode_input in episode_inputs:
        if not isinstance(episode_input, dict):
            raise ValueError("segmentation calibration episode input is invalid")
        semantic_input = {
            key: episode_input.get(key)
            for key in (
                "segmentation_version",
                "source_id",
                "user_event_ids",
                "intervening_event_ids",
                "boundary_reference_ids",
            )
        }
        input_id = episode_input.get("episode_evidence_input_id")
        boundary_ids = semantic_input["boundary_reference_ids"]
        if (
            input_id != digest_json(semantic_input)
            or not isinstance(boundary_ids, list)
            or not set(boundary_ids) <= reference_ids
        ):
            raise ValueError("segmentation calibration episode input identity is invalid")
        input_ids.add(str(input_id))
    if len(input_ids) != len(episode_inputs):
        raise ValueError("segmentation calibration episode inputs are duplicated")

    references_by_case = {reference["pilot_case_id"]: reference for reference in references}
    expected_inputs = []
    source_rows = {source["source_id"]: source for source in corpus["sources"]["sources"]}
    for source_id in sorted(source_rows):
        source = source_rows[source_id]
        source_cases = sorted(
            (case for case in corpus["manifest"]["cases"] if case["source_id"] == source_id),
            key=lambda case: case["region"],
        )
        split_after = {
            case["region"]
            for case in source_cases
            if references_by_case[case["case_id"]]["answer"] == "split"
        }
        start = 0
        for end in [region + 1 for region in sorted(split_after)] + [len(source["user_turns"])]:
            semantic_input = {
                "segmentation_version": "segmentation-v2",
                "source_id": source_id,
                "user_event_ids": [turn["event_id"] for turn in source["user_turns"][start:end]],
                "intervening_event_ids": [
                    source["intervening_events"][index]["event_id"]
                    for index in range(start, max(start, end - 1))
                ],
                "boundary_reference_ids": [
                    references_by_case[case["case_id"]]["boundary_reference_id"]
                    for case in source_cases
                    if start <= case["region"] < end - 1
                ],
            }
            expected_inputs.append(
                {"episode_evidence_input_id": digest_json(semantic_input), **semantic_input}
            )
            start = end
    if episode_inputs != expected_inputs:
        raise ValueError("segmentation calibration episode inputs are not reference-derived")
    return document


def load_boundary_pilot(manifest_path: Path) -> tuple[BoundaryPacket, ...]:
    manifest = json.loads(manifest_path.read_text())
    source_path = manifest_path.parent / str(manifest["source_corpus"])
    source_document = json.loads(source_path.read_text())
    return load_boundary_pilot_bytes(
        (canonical_json({"manifest": manifest, "sources": source_document}) + "\n").encode()
    )


def load_boundary_pilot_bytes(corpus_bytes: bytes) -> tuple[BoundaryPacket, ...]:
    corpus = json.loads(corpus_bytes)
    manifest = corpus["manifest"]
    source_document = corpus["sources"]
    source_rows = source_document["sources"]
    source_ids = [str(row["source_id"]) for row in source_rows]
    case_ids = [str(row["case_id"]) for row in manifest["cases"]]
    if len(source_ids) != len(set(source_ids)) or len(case_ids) != len(set(case_ids)):
        raise ValueError("pilot source and case identities must be unique")
    for source in source_rows:
        event_ids = [
            str(row["event_id"]) for row in source["user_turns"] + source["intervening_events"]
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("pilot source event identities must be unique")
    sources = {str(row["source_id"]): row for row in source_rows}
    packets: list[BoundaryPacket] = []
    seen_regions: set[tuple[str, int]] = set()
    for case in manifest["cases"]:
        source_id = str(case["source_id"])
        region = int(case["region"])
        key = (source_id, region)
        if key in seen_regions:
            raise ValueError("pilot contains a duplicate boundary region")
        seen_regions.add(key)
        source = sources.get(source_id)
        if source is None:
            raise ValueError("pilot source is missing")
        turns = source["user_turns"]
        intervening_events = source["intervening_events"]
        if (
            len(intervening_events) != len(turns) - 1
            or region < 0
            or region >= len(intervening_events)
        ):
            raise ValueError("pilot region does not resolve to adjacent user turns")
        validate_pilot_strata(case, source, region)
        left = turns[region]
        right = turns[region + 1]
        packet_seed = digest_json(
            {
                "manifest_version": manifest["manifest_version"],
                "case_id": case["case_id"],
                "source_id": source_id,
                "region": region,
            }
        )
        adjacent = [
            pilot_event(packet_seed, left),
            pilot_event(packet_seed, right),
        ]
        intervening = intervening_events[region]
        length_stratum = next(
            (value for value in case["strata"] if value in {"short", "medium", "long"}),
            "short",
        )
        context_events: list[PacketEvent] = []
        if length_stratum == "medium":
            for turn_index in (region - 1, region + 2):
                if 0 <= turn_index < len(turns):
                    context_events.append(pilot_event(packet_seed, turns[turn_index]))
            for event_index in (region - 1, region + 1):
                if 0 <= event_index < len(intervening_events):
                    context_events.append(
                        pilot_normalized_event(packet_seed, intervening_events[event_index])
                    )
        elif length_stratum == "long":
            context_events.extend(
                pilot_event(packet_seed, turn)
                for index, turn in enumerate(turns)
                if index not in {region, region + 1}
            )
            context_events.extend(
                pilot_normalized_event(packet_seed, event)
                for index, event in enumerate(intervening_events)
                if index != region
            )
        packet = BoundaryPacket(
            packet_id="",
            left_user_event_id=adjacent[0].evidence_id,
            right_user_event_id=adjacent[1].evidence_id,
            adjacent_user_turns=adjacent,
            intervening_normalized_events=[pilot_normalized_event(packet_seed, intervening)],
            bounded_context_events=context_events,
        )
        packet_id = digest_json(
            {
                "pilot_case_id": case["case_id"],
                "judge_packet": packet.model_dump(mode="json"),
            }
        )
        packets.append(packet.model_copy(update={"packet_id": packet_id}))
    if len({packet.packet_id for packet in packets}) != len(packets):
        raise ValueError("pilot packet identities are not unique")
    return tuple(packets)


def export_boundary_pilot(
    corpus_bytes: bytes,
    snapshot_bundle_id: str,
) -> tuple[EvaluationPacketExport, ...]:
    if corpus_bytes != boundary_pilot_corpus_bytes():
        raise ValueError("pilot corpus does not match the checked 24-case corpus")
    corpus = json.loads(corpus_bytes)
    manifest = corpus["manifest"]
    source_document = corpus["sources"]
    corpus_content_id = digest_json({"manifest": manifest, "sources": source_document})
    packets = load_boundary_pilot_bytes(corpus_bytes)
    exports = []
    for packet in packets:
        targets: list[str] = []
        unsigned_packet = packet.model_copy(update={"packet_id": ""})
        packet_id = digest_json(
            {
                "pilot_corpus_content_id": corpus_content_id,
                "evaluation_corpus_id": manifest["manifest_version"],
                "judge_packet": unsigned_packet.model_dump(mode="json"),
            }
        )
        packet = packet.model_copy(update={"packet_id": packet_id})
        judge_json = canonical_json(packet.model_dump(mode="json"))
        exports.append(
            EvaluationPacketExport(
                routing=RoutingEnvelope(
                    packet_id=packet_id,
                    packet_kind=PacketKind.BOUNDARY,
                    evaluation_corpus_id=str(manifest["manifest_version"]),
                    normalization_run_id=None,
                    snapshot_bundle_id=snapshot_bundle_id,
                    target_model_identities=targets,
                    excluded_judge_identities=targets,
                    identity_exposure_status=identity_exposure_status(judge_json, targets),
                    judge_packet_hash=hashlib.sha256(judge_json.encode()).hexdigest(),
                ),
                judge_packet=packet,
            )
        )
    return tuple(exports)


def pilot_event(packet_seed: str, turn: dict[str, object]) -> PacketEvent:
    event_id = str(turn["event_id"])
    text = str(turn["text"])
    opaque_id = opaque_evidence_id(packet_seed, event_id)
    return PacketEvent(
        evidence_id=opaque_id,
        source_event_id=opaque_id,
        entity_kind="normalized_message",
        role="user",
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        text_length=len(text),
    )


def pilot_normalized_event(packet_seed: str, event: dict) -> PacketEvent:
    event_id = opaque_evidence_id(packet_seed, str(event["event_id"]))
    text = str(event["text"]) if "text" in event else None
    return PacketEvent(
        evidence_id=event_id,
        source_event_id=event_id,
        entity_kind=str(event["entity_kind"]),
        role=str(event["role"]) if "role" in event else None,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
        text_length=len(text) if text is not None else None,
        structure={str(key): value for key, value in event.get("structure", {}).items()},
    )


def validate_pilot_strata(case: dict, source: dict, region: int) -> None:
    strata = set(case["strata"])
    if source.get("adapter") not in strata:
        raise ValueError("pilot adapter stratum does not match its source")
    metadata_rows = source.get("region_metadata")
    if not isinstance(metadata_rows, list) or len(metadata_rows) != len(
        source["intervening_events"]
    ):
        raise ValueError("pilot source region metadata is incomplete")
    metadata = metadata_rows[region]
    expected_capture = (
        "snapshot_incomplete"
        if "incomplete" in strata
        else "possibly_active"
        if "active" in strata
        else None
    )
    if expected_capture is not None and metadata.get("capture_state") != expected_capture:
        raise ValueError("pilot capture-state stratum lacks source provenance")
    if "prior_disagreement" in strata and metadata.get("prior_annotation_status") != "disputed":
        raise ValueError("pilot disagreement stratum lacks source provenance")
    if "task_switch" in strata and metadata.get("task_transition") != "explicit":
        raise ValueError("pilot task-switch stratum lacks source provenance")
    structure = source["intervening_events"][region].get("structure", {})
    if "success" in strata and structure.get("exit_code") != 0:
        raise ValueError("pilot success stratum lacks successful source evidence")
    if ("blocker" in strata or "tool_error" in strata) and structure.get("exit_code") != 1:
        raise ValueError("pilot error stratum lacks failing source evidence")
    if "delegation" in strata and structure.get("tool_name") != "delegate":
        raise ValueError("pilot delegation stratum lacks source evidence")
    if (
        "compaction" in strata
        and source["intervening_events"][region].get("entity_kind") != "normalized_compaction"
    ):
        raise ValueError("pilot compaction stratum lacks source evidence")
