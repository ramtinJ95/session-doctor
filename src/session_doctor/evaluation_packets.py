from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
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
    packet = BoundaryPacket(
        packet_id="",
        left_user_event_id=opaque_evidence_id(
            packet_seed, left.source_event_id or left.evidence_id
        ),
        right_user_event_id=opaque_evidence_id(
            packet_seed, right.source_event_id or right.evidence_id
        ),
        adjacent_user_turns=[
            blind_event(left, redaction_terms, packet_seed),
            blind_event(right, redaction_terms, packet_seed),
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
    rows: list[tuple[int, int, str, PacketEvent]] = []

    def add(
        evidence_id: str,
        entity_kind: str,
        source_event_id: str | None,
        entity_order: int,
        *,
        role: str | None = None,
        text: str | None = None,
        text_hash: str | None = None,
        text_length: int | None = None,
        structure: dict[str, object] | None = None,
    ) -> None:
        rows.append(
            (
                record_indexes.get(source_event_id or "", 2**31 - 1),
                entity_order,
                evidence_id,
                PacketEvent(
                    evidence_id=evidence_id,
                    source_event_id=source_event_id,
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
            index,
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
            index,
            structure={"name": call.name},
        )
    for index, result in enumerate(bundle.tool_results):
        add(
            result.tool_result_id,
            "tool_result",
            result.source_event_id,
            index,
            structure={"is_error": result.is_error, "output_length": result.output_length},
        )
    for index, command in enumerate(bundle.command_runs):
        add(
            command.command_run_id,
            "command_run",
            command.source_event_id,
            index,
            structure={"exit_code": command.exit_code, "output_length": command.output_length},
        )
    for index, activity in enumerate(bundle.file_activities):
        add(
            activity.file_activity_id,
            "file_activity",
            activity.source_event_id,
            index,
            structure={"operation": activity.operation},
        )
    return [row[3] for row in sorted(rows, key=lambda row: row[:3])]


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
    return targets, tuple(
        sorted(
            (term for term in terms if len(term) >= 3),
            key=lambda term: len(term),
            reverse=True,
        )
    )


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
                opaque_evidence_id(packet_seed, event.source_event_id)
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
        os.replace(temporary, output_directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


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
    sources = {str(row["source_id"]): row for row in source_document["sources"]}
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
        intervening_id = opaque_evidence_id(packet_seed, str(intervening["event_id"]))
        packet = BoundaryPacket(
            packet_id="",
            left_user_event_id=adjacent[0].evidence_id,
            right_user_event_id=adjacent[1].evidence_id,
            adjacent_user_turns=adjacent,
            intervening_normalized_events=[
                PacketEvent(
                    evidence_id=intervening_id,
                    source_event_id=intervening_id,
                    entity_kind=str(intervening["entity_kind"]),
                    role=(str(intervening["role"]) if "role" in intervening else None),
                    text=(str(intervening["text"]) if "text" in intervening else None),
                    structure={
                        str(key): value for key, value in intervening.get("structure", {}).items()
                    },
                )
            ],
            bounded_context_events=[],
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
    sources = {str(row["source_id"]): row for row in source_document["sources"]}
    packets = load_boundary_pilot_bytes(corpus_bytes)
    exports = []
    for case, packet in zip(manifest["cases"], packets, strict=True):
        source = sources[str(case["source_id"])]
        targets = [
            canonical_json(
                {
                    "model": str(source["target_model"]),
                    "provider": str(source["target_provider"]),
                }
            )
        ]
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
