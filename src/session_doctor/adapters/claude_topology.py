from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from session_doctor.ids import stable_id
from session_doctor.privacy import hash_text
from session_doctor.schemas import AgentName, SessionSource, SourceKind

from .common import content_blocks, dict_value, hash_json, int_value, string_value


@dataclass
class TranscriptFacts:
    source: SessionSource
    native_session_ids: list[str] = field(default_factory=list)
    event_ids: set[str] = field(default_factory=set)
    tool_use_ids: set[str] = field(default_factory=set)
    agent_ids: set[str] = field(default_factory=set)
    source_assistant_ids: set[str] = field(default_factory=set)
    persisted_output_paths: set[str] = field(default_factory=set)
    read_status: str = "ok"


def enrich_claude_sources(sources: list[SessionSource]) -> None:
    transcripts = {
        Path(source.source_path).resolve(): transcript_facts(source)
        for source in sources
        if source.source_kind in {SourceKind.ROOT_SESSION, SourceKind.SUBSESSION}
    }
    metadata_paths = {
        Path(source.source_path).resolve()
        for source in sources
        if source.source_kind is SourceKind.SUBAGENT_METADATA
    }
    tool_result_paths = {
        Path(source.source_path).resolve()
        for source in sources
        if source.source_kind is SourceKind.TOOL_RESULT
    }

    for facts in transcripts.values():
        add_transcript_metadata(facts)
        if facts.source.source_kind is SourceKind.SUBSESSION:
            enrich_subagent(facts, transcripts, metadata_paths)

    add_nesting_depths(transcripts)
    add_orphan_counts(transcripts, metadata_paths, tool_result_paths)


def transcript_facts(source: SessionSource) -> TranscriptFacts:
    facts = TranscriptFacts(source=source)
    path = Path(source.source_path)
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    facts.read_status = "partial"
                    continue
                if not isinstance(record, dict):
                    facts.read_status = "partial"
                    continue
                collect_record_facts(facts, record)
    except (OSError, UnicodeDecodeError):
        facts.read_status = "unreadable"
    return facts


def collect_record_facts(facts: TranscriptFacts, record: dict[str, Any]) -> None:
    native_session_id = string_value(record.get("sessionId"))
    if native_session_id is not None and native_session_id not in facts.native_session_ids:
        facts.native_session_ids.append(native_session_id)
    for key, target in (
        ("uuid", facts.event_ids),
        ("agentId", facts.agent_ids),
        ("sourceToolAssistantUUID", facts.source_assistant_ids),
    ):
        value = string_value(record.get(key))
        if value is not None:
            target.add(value)

    message = dict_value(record.get("message"))
    for block in content_blocks(message.get("content")):
        if string_value(block.get("type")) != "tool_use":
            continue
        tool_use_id = string_value(block.get("id"))
        if tool_use_id is not None:
            facts.tool_use_ids.add(tool_use_id)

    persisted_output_path = string_value(
        dict_value(record.get("toolUseResult")).get("persistedOutputPath")
    )
    if persisted_output_path is not None:
        facts.persisted_output_paths.add(persisted_output_path)


def add_transcript_metadata(facts: TranscriptFacts) -> None:
    facts.source.metadata.update(
        {
            "claude_topology_read_status": facts.read_status,
            "claude_agent_ids": sorted(facts.agent_ids),
            "claude_source_tool_assistant_uuids": sorted(facts.source_assistant_ids),
            "claude_referenced_tool_result_count": len(facts.persisted_output_paths),
        }
    )


def enrich_subagent(
    facts: TranscriptFacts,
    transcripts: dict[Path, TranscriptFacts],
    metadata_paths: set[Path],
) -> None:
    source_path = Path(facts.source.source_path).resolve()
    metadata_path = source_path.with_suffix(".meta.json")
    sidecar = read_subagent_metadata(metadata_path, metadata_path in metadata_paths)
    metadata_agent_id = string_value(sidecar.get("agent_id"))
    if metadata_agent_id is not None and facts.agent_ids:
        sidecar["identity_status"] = (
            "matched" if metadata_agent_id in facts.agent_ids else "mismatched"
        )
    else:
        sidecar["identity_status"] = "unknown"
    facts.source.metadata["claude_subagent_metadata"] = sidecar

    parent_agent_id = string_value(sidecar.get("parent_agent_id"))
    agent_candidates = matching_sources(
        transcripts,
        facts,
        {parent_agent_id} if parent_agent_id is not None else set(),
        attribute="agent_ids",
    )
    tool_use_id = string_value(sidecar.get("tool_use_id"))
    tool_candidates = matching_sources(
        transcripts,
        facts,
        {tool_use_id} if tool_use_id is not None else set(),
        attribute="tool_use_ids",
    )
    candidates, status = resolve_parent_candidates(
        agent_candidates,
        tool_candidates,
        has_parent_agent_signal=parent_agent_id is not None,
        has_tool_signal=tool_use_id is not None,
    )
    native_spawn_depth = int_value(sidecar.get("spawn_depth"))
    if status == "missing" and native_spawn_depth == 0:
        root_candidates = {
            path
            for path, candidate in transcripts.items()
            if candidate.source.source_kind is SourceKind.ROOT_SESSION
            and claude_session_directory(path) == claude_session_directory(source_path)
        }
        if len(root_candidates) == 1:
            candidates, status = root_candidates, "linked"
        elif len(root_candidates) > 1:
            candidates, status = root_candidates, "ambiguous"
    facts.source.metadata["claude_parent_link_status"] = status
    facts.source.metadata["claude_parent_candidate_count"] = len(candidates)
    if len(candidates) != 1 or status != "linked":
        facts.source.parent_source_id = None
        return

    parent_path = next(iter(candidates))
    parent = transcripts[parent_path]
    facts.source.parent_source_id = parent.source.source_id
    facts.source.metadata["claude_parent_session_id"] = session_id_for_facts(parent)


def read_subagent_metadata(path: Path, discovered: bool) -> dict[str, Any]:
    if not discovered:
        return {"status": "missing", "present": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "malformed", "present": True}
    if not isinstance(payload, dict):
        return {"status": "malformed", "present": True}

    description = string_value(payload.get("description"))
    permission = dict_value(payload.get("permission"))
    return {
        "status": "correlated",
        "present": True,
        "content_hash": hash_json(payload),
        "keys": sorted(payload),
        "agent_type": first_string(payload, "agentType", "agent_type"),
        "task_kind": first_string(payload, "taskKind", "task_kind"),
        "model": string_value(payload.get("model")),
        "permission_mode": first_string(payload, "permissionMode", "permission_mode"),
        "permission_keys": sorted(permission),
        "nesting_depth": first_int(payload, "nestingDepth", "nesting_depth"),
        "spawn_depth": first_int(payload, "spawnDepth", "spawn_depth"),
        "tool_use_id": first_string(payload, "toolUseId", "tool_use_id"),
        "parent_agent_id": first_string(payload, "parentAgentId", "parent_agent_id"),
        "agent_id": first_string(payload, "agentId", "agent_id"),
        "description_hash": hash_text(description) if description is not None else None,
        "description_length": len(description) if description is not None else None,
    }


def matching_sources(
    transcripts: dict[Path, TranscriptFacts],
    child: TranscriptFacts,
    identities: set[str],
    *,
    attribute: str,
) -> set[Path]:
    if not identities:
        return set()
    child_session_dir = claude_session_directory(Path(child.source.source_path))
    return {
        path
        for path, candidate in transcripts.items()
        if candidate is not child
        and claude_session_directory(path) == child_session_dir
        and identities.intersection(getattr(candidate, attribute))
    }


def resolve_parent_candidates(
    agent_candidates: set[Path],
    tool_candidates: set[Path],
    *,
    has_parent_agent_signal: bool,
    has_tool_signal: bool,
) -> tuple[set[Path], str]:
    if (
        has_parent_agent_signal
        and has_tool_signal
        and (not agent_candidates or not tool_candidates)
    ):
        return agent_candidates.union(tool_candidates), "mismatched"
    if agent_candidates and tool_candidates:
        intersection = agent_candidates.intersection(tool_candidates)
        if not intersection:
            return agent_candidates.union(tool_candidates), "mismatched"
        candidates = intersection
    else:
        candidates = agent_candidates or tool_candidates

    if len(candidates) == 1:
        return candidates, "linked"
    if len(candidates) > 1:
        return candidates, "ambiguous"
    if has_parent_agent_signal or has_tool_signal:
        return set(), "mismatched"
    return set(), "missing"


def session_id_for_facts(facts: TranscriptFacts) -> str:
    native_session_id = facts.native_session_ids[0] if facts.native_session_ids else None
    source_path = Path(facts.source.source_path)
    native_identity = native_session_id or facts.source.native_session_id or source_path.stem
    return stable_id(
        "session",
        AgentName.CLAUDE.value,
        facts.source.source_path,
        native_identity,
    )


def add_nesting_depths(transcripts: dict[Path, TranscriptFacts]) -> None:
    by_source_id = {facts.source.source_id: facts for facts in transcripts.values()}
    topology = {
        facts.source.source_id: nesting_depth(facts, by_source_id)
        for facts in transcripts.values()
        if facts.source.source_kind is SourceKind.SUBSESSION
    }
    for facts in transcripts.values():
        if facts.source.source_kind is not SourceKind.SUBSESSION:
            continue
        depth, cyclic = topology[facts.source.source_id]
        if cyclic:
            facts.source.parent_source_id = None
            facts.source.metadata.pop("claude_parent_session_id", None)
            facts.source.metadata["claude_parent_link_status"] = "cyclic"
        facts.source.metadata["claude_nesting_depth"] = depth


def nesting_depth(
    facts: TranscriptFacts,
    by_source_id: dict[str, TranscriptFacts],
) -> tuple[int | None, bool]:
    depth = 0
    current = facts
    visited: set[str] = set()
    while current.source.source_kind is SourceKind.SUBSESSION:
        if current.source.source_id in visited or current.source.parent_source_id is None:
            return None, current.source.source_id in visited
        visited.add(current.source.source_id)
        parent = by_source_id.get(current.source.parent_source_id)
        if parent is None:
            return None, False
        depth += 1
        current = parent
    return depth, False


def add_orphan_counts(
    transcripts: dict[Path, TranscriptFacts],
    metadata_paths: set[Path],
    tool_result_paths: set[Path],
) -> None:
    transcript_paths = set(transcripts)
    orphan_metadata = {
        path
        for path in metadata_paths
        if path.with_suffix("").with_suffix(".jsonl") not in transcript_paths
    }
    referenced_results = {
        resolved
        for facts in transcripts.values()
        for raw_path in facts.persisted_output_paths
        if (resolved := resolve_tool_result_path(Path(facts.source.source_path), raw_path))
        is not None
    }
    orphan_results = tool_result_paths - referenced_results
    malformed_orphan_metadata = {
        path
        for path in orphan_metadata
        if read_subagent_metadata(path, True)["status"] == "malformed"
    }

    for path, facts in transcripts.items():
        if facts.source.source_kind is not SourceKind.ROOT_SESSION:
            continue
        session_dir = path.parent / path.stem
        facts.source.metadata["claude_orphan_subagent_metadata_count"] = sum(
            candidate.is_relative_to(session_dir) for candidate in orphan_metadata
        )
        facts.source.metadata["claude_orphan_tool_result_count"] = sum(
            candidate.is_relative_to(session_dir) for candidate in orphan_results
        )
        facts.source.metadata["claude_malformed_orphan_metadata_count"] = sum(
            candidate.is_relative_to(session_dir) for candidate in malformed_orphan_metadata
        )


def resolve_tool_result_path(source_path: Path, raw_path: str) -> Path | None:
    session_dir = claude_session_directory(source_path)
    tool_results_dir = (session_dir / "tool-results").resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = session_dir / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved.is_relative_to(tool_results_dir):
        return resolved
    if candidate.parent.name != "tool-results" or candidate.parent.parent.name != session_dir.name:
        return None
    copied_candidate = (tool_results_dir / candidate.name).resolve()
    return copied_candidate if copied_candidate.is_relative_to(tool_results_dir) else None


def claude_session_directory(source_path: Path) -> Path:
    source_path = source_path.resolve()
    if source_path.parent.name == "subagents":
        return source_path.parent.parent
    return source_path.parent / source_path.stem


def first_string(payload: dict[str, Any], *keys: str) -> str | None:
    return next(
        (value for key in keys if (value := string_value(payload.get(key))) is not None),
        None,
    )


def first_int(payload: dict[str, Any], *keys: str) -> int | None:
    return next(
        (value for key in keys if (value := int_value(payload.get(key))) is not None),
        None,
    )


__all__ = [
    "claude_session_directory",
    "enrich_claude_sources",
    "resolve_tool_result_path",
]
