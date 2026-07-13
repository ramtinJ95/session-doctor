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
from typing import TYPE_CHECKING

from session_doctor.evaluation_models import (
    ANNOTATION_PROTOCOL_VERSION,
    EVALUATION_SCHEMA_VERSION,
    BoundaryPacket,
    EvaluationPacketExport,
    IdentityExposureStatus,
    PacketEvent,
    PacketKind,
    RoutingEnvelope,
)
from session_doctor.schemas import Message, NormalizedRole, SemanticFoundation

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
    message_order = normalized_message_order(bundle.messages, record_indexes)
    rows: list[tuple[Fraction, int, int, str, PacketEvent]] = []

    def add(
        evidence_id: str,
        entity_kind: str,
        source_event_id: str | None,
        entity_rank: int,
        entity_order: int,
        fallback_order: Fraction | None = None,
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
                Fraction(record_indexes[resolved_source_event_id])
                if resolved_source_event_id is not None
                else fallback_order
                if fallback_order is not None
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
            fallback_order=message_order[message.message_id],
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
            structure={"name": call.name},
        )
    for index, result in enumerate(bundle.tool_results):
        add(
            result.tool_result_id,
            "tool_result",
            result.source_event_id,
            2,
            index,
            structure={"is_error": result.is_error, "output_length": result.output_length},
        )
    for index, command in enumerate(bundle.command_runs):
        add(
            command.command_run_id,
            "command_run",
            command.source_event_id,
            3,
            index,
            structure={"exit_code": command.exit_code, "output_length": command.output_length},
        )
    for index, activity in enumerate(bundle.file_activities):
        add(
            activity.file_activity_id,
            "file_activity",
            activity.source_event_id,
            4,
            index,
            structure={"operation": activity.operation},
        )
    return [row[4] for row in sorted(rows, key=lambda row: row[:4])]


def normalized_message_order(
    messages: list[Message],
    record_indexes: dict[str, int],
) -> dict[str, Fraction]:
    resolved = {
        index: record_indexes[message.source_event_id]
        for index, message in enumerate(messages)
        if message.source_event_id in record_indexes
    }
    order: dict[str, Fraction] = {}
    for index, message in enumerate(messages):
        if index in resolved:
            order[message.message_id] = Fraction(resolved[index])
            continue
        previous = max((row for row in resolved if row < index), default=None)
        following = min((row for row in resolved if row > index), default=None)
        if previous is not None and following is not None:
            span_count = following - previous
            offset = index - previous
            order[message.message_id] = Fraction(resolved[previous]) + Fraction(
                (resolved[following] - resolved[previous]) * offset,
                span_count,
            )
        elif previous is not None:
            order[message.message_id] = Fraction(resolved[previous] + index - previous)
        elif following is not None:
            order[message.message_id] = Fraction(resolved[following] - (following - index))
        else:
            order[message.message_id] = Fraction(index)
    return order


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
