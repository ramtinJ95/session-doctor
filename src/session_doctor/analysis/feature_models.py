from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from session_doctor.schemas import CommandRun, MessageFeature, SessionFeature, ToolResult


@dataclass(frozen=True)
class ExtractedFeatures:
    message_features: list[MessageFeature]
    session_features: list[SessionFeature]


@dataclass(frozen=True)
class RequestSignature:
    normalized_text: str
    tokens: tuple[str, ...]
    token_set: frozenset[str]
    bigrams: frozenset[tuple[str, str]]
    char_grams: frozenset[str]


@dataclass(frozen=True)
class SessionFeatureContext:
    session_id: str
    message_features: list[MessageFeature]
    message_feature_counts: Counter[str]
    failed_commands: list[CommandRun]
    failed_tool_results: list[ToolResult]
    repeated_failures: list[dict[str, object]]
    repeated_command_failures: list[dict[str, object]]
    repeated_command_failure_count: int
    file_edit_counts: Counter[str]
    file_edit_events: dict[str, list[str]]
    repeated_file_edits: dict[str, int]
    repeated_file_edit_events: dict[str, list[str]]
    unresolved_evidence: dict[str, object]
