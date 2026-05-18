from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    Message,
    MessageFeature,
    NormalizedRole,
    SessionFeature,
)

REPEAT_REQUEST_SIMILARITY_THRESHOLD = 0.35
EXACT_NORMALIZED_TEXT_BOOST = 0.10
MINIMUM_COMPARABLE_TOKEN_COUNT = 4
ENDING_WINDOW_MIN_EVENTS = 5
ENDING_WINDOW_MAX_EVENTS = 20
ENDING_WINDOW_FRACTION = 0.20
ENDING_WINDOW_MINUTES = 10
MUTATING_FILE_OPERATIONS = frozenset(
    {"create", "delete", "edit", "move", "patch", "rename", "update", "write"}
)

STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "be",
    "can",
    "could",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "please",
    "should",
    "that",
    "the",
    "these",
    "this",
    "to",
    "too",
    "we",
    "what",
    "with",
    "would",
    "you",
}

SYNONYMS = {
    "decided": "decision",
    "decides": "decision",
    "doc": "plan",
    "docs": "plan",
    "document": "plan",
    "failed": "fail",
    "failing": "fail",
    "failure": "fail",
    "fixed": "fix",
    "fixes": "fix",
    "parsed": "parse",
    "parsing": "parse",
    "tests": "test",
    "warnings": "warning",
}

CORRECTION_MARKERS = {
    "not what i asked": "not_what_i_asked",
    "that is not what i meant": "not_what_i_meant",
    "we already tried": "already_tried",
    "i meant": "clarification_correction",
    "you misunderstood": "misunderstood",
    "why are you": "unexpected_action",
    "stop doing": "stop_action",
    "still broken": "still_broken",
    "wrong": "wrong",
}

FRUSTRATION_MARKERS = {
    "still broken": "still_broken",
    "this is wrong": "wrong",
    "already tried": "already_tried",
    "too many warnings": "too_many_warnings",
    "not good": "not_good",
    "be thorough": "high_stakes",
    "very important": "high_stakes",
    "again": "again",
    "why": "why",
}

SCOPE_BOUNDARY_MARKERS = {
    "do not": "do_not",
    "don't": "do_not",
    "dont": "do_not",
    "no need to": "no_need",
    "before you": "ordering_boundary",
    "not yet": "not_yet",
    "keep it": "keep_scope",
    "small commits": "small_commits",
    "only": "only",
    "just": "just",
    "defer": "defer",
}


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


def analyze_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
) -> ExtractedFeatures:
    if bundle.session is None:
        msg = "Cannot analyze features without a session record."
        raise ValueError(msg)

    message_features: list[MessageFeature] = []
    message_features.extend(repeated_request_features(bundle.messages, analysis_run_id))
    message_features.extend(marker_features(bundle.messages, analysis_run_id))

    session_features = session_count_features(bundle, analysis_run_id, message_features)
    return ExtractedFeatures(
        message_features=message_features,
        session_features=session_features,
    )


def repeated_request_features(
    messages: list[Message],
    analysis_run_id: str,
) -> list[MessageFeature]:
    features: list[MessageFeature] = []
    previous_user_messages: list[tuple[Message, RequestSignature]] = []
    for message in messages:
        if message.role != NormalizedRole.USER or not message.text:
            continue
        signature = request_signature(message.text)
        if len(signature.tokens) < MINIMUM_COMPARABLE_TOKEN_COUNT:
            previous_user_messages.append((message, signature))
            continue

        best_match: tuple[Message, float] | None = None
        for previous_message, previous_signature in previous_user_messages:
            score = signature_similarity(signature, previous_signature)
            if best_match is None or score > best_match[1]:
                best_match = (previous_message, score)

        if best_match and best_match[1] >= REPEAT_REQUEST_SIMILARITY_THRESHOLD:
            matched_message, score = best_match
            features.append(
                message_feature(
                    analysis_run_id=analysis_run_id,
                    message=message,
                    feature_name="repeat_request_similarity",
                    feature_value=f"{score:.3f}",
                    score=score,
                    evidence={
                        "matched_message_id": matched_message.message_id,
                        "matched_source_event_id": matched_message.source_event_id,
                        "similarity_score": round(score, 3),
                        "threshold": REPEAT_REQUEST_SIMILARITY_THRESHOLD,
                    },
                )
            )

        previous_user_messages.append((message, signature))
    return features


def marker_features(
    messages: list[Message],
    analysis_run_id: str,
) -> list[MessageFeature]:
    features: list[MessageFeature] = []
    marker_groups = (
        ("correction_marker", CORRECTION_MARKERS),
        ("frustration_marker", FRUSTRATION_MARKERS),
        ("scope_boundary_marker", SCOPE_BOUNDARY_MARKERS),
    )
    for message in messages:
        if message.role != NormalizedRole.USER or not message.text:
            continue
        text = normalized_marker_text(message.text)
        for feature_name, markers in marker_groups:
            matched_marker_families: defaultdict[str, list[str]] = defaultdict(list)
            for marker, marker_family in markers.items():
                if marker_matches(text, marker):
                    matched_marker_families[marker_family].append(marker)
            for marker_family, matched_markers in matched_marker_families.items():
                features.append(
                    message_feature(
                        analysis_run_id=analysis_run_id,
                        message=message,
                        feature_name=feature_name,
                        feature_value=marker_family,
                        evidence={"matched_markers": sorted(matched_markers)},
                    )
                )
    return features


def session_count_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    message_features: list[MessageFeature],
) -> list[SessionFeature]:
    assert bundle.session is not None
    session_id = bundle.session.session_id
    feature_counts = message_feature_counts(message_features)
    failed_commands = [
        command
        for command in bundle.command_runs
        if command.exit_code is not None and command.exit_code != 0
    ]
    failed_tool_results = [result for result in bundle.tool_results if result.is_error is True]
    repeated_failures = repeated_failure_groups(bundle)
    repeated_command_failures = repeated_command_loop_failure_groups(repeated_failures)
    repeated_command_failure_count = repeated_failure_max_repeat_count(
        repeated_command_failures,
    )
    file_edit_counts = Counter(
        activity.path
        for activity in bundle.file_activities
        if activity.operation in MUTATING_FILE_OPERATIONS
    )
    repeated_file_edits = {path: count for path, count in file_edit_counts.items() if count > 1}
    repeated_file_edit_events = repeated_file_edit_source_events(bundle)
    unresolved_evidence = unresolved_ending_evidence(bundle, message_features)

    return [
        session_feature(
            analysis_run_id,
            session_id,
            "user_message_count",
            count_messages(bundle.messages, NormalizedRole.USER),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "assistant_message_count",
            count_messages(bundle.messages, NormalizedRole.ASSISTANT),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "repeat_request_count",
            feature_counts["repeat_request_similarity"],
            evidence=feature_evidence(message_features, "repeat_request_similarity"),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "correction_count",
            feature_counts["correction_marker"],
            evidence=feature_evidence(message_features, "correction_marker"),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "frustration_count",
            feature_counts["frustration_marker"],
            evidence=feature_evidence(message_features, "frustration_marker"),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "scope_boundary_count",
            feature_counts["scope_boundary_marker"],
            evidence=feature_evidence(message_features, "scope_boundary_marker"),
        ),
        session_feature(analysis_run_id, session_id, "command_count", len(bundle.command_runs)),
        session_feature(
            analysis_run_id,
            session_id,
            "failed_command_count",
            len(failed_commands),
            evidence={
                "command_run_ids": [command.command_run_id for command in failed_commands],
                "source_event_ids": [
                    command.source_event_id
                    for command in failed_commands
                    if command.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "failed_command_ratio",
            ratio(len(failed_commands), len(bundle.command_runs)),
            score=ratio(len(failed_commands), len(bundle.command_runs)),
        ),
        session_feature(analysis_run_id, session_id, "tool_result_count", len(bundle.tool_results)),
        session_feature(
            analysis_run_id,
            session_id,
            "failed_tool_result_count",
            len(failed_tool_results),
            evidence={
                "tool_result_ids": [result.tool_result_id for result in failed_tool_results],
                "source_event_ids": [
                    result.source_event_id
                    for result in failed_tool_results
                    if result.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "failed_tool_result_ratio",
            ratio(len(failed_tool_results), len(bundle.tool_results)),
            score=ratio(len(failed_tool_results), len(bundle.tool_results)),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "repeated_failure_count",
            sum(group["repeat_count"] for group in repeated_failures),
            evidence={
                "groups": repeated_failures,
                "source_event_ids": repeated_failure_source_event_ids(repeated_failures),
            },
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "repeated_command_failure_count",
            repeated_command_failure_count,
            evidence={
                "groups": repeated_command_failures,
                "source_event_ids": repeated_failure_source_event_ids(repeated_command_failures),
            },
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "edited_file_count",
            len(file_edit_counts),
            evidence={"paths": sorted(file_edit_counts)},
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "same_file_edited_repeatedly_count",
            len(repeated_file_edits),
            evidence={
                "paths": repeated_file_edits,
                "source_event_ids_by_path": repeated_file_edit_events,
                "source_event_ids": sorted(
                    {
                        source_event_id
                        for source_event_ids in repeated_file_edit_events.values()
                        for source_event_id in source_event_ids
                    }
                ),
            },
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "max_edits_to_single_file",
            max(file_edit_counts.values(), default=0),
        ),
        session_feature(
            analysis_run_id,
            session_id,
            "unresolved_ending_signal",
            bool(unresolved_evidence),
            score=1.0 if unresolved_evidence else 0.0,
            evidence=unresolved_evidence,
        ),
    ]


def repeated_failure_groups(bundle: ParsedSessionBundle) -> list[dict[str, object]]:
    group_values: defaultdict[tuple[str, str], list[tuple[str, str | None]]] = defaultdict(list)
    for command in bundle.command_runs:
        if command.exit_code is None or command.exit_code == 0:
            continue
        if command.stderr_hash:
            group_values[("command_stderr_hash", f"stderr_hash:{command.stderr_hash}")].append(
                (command.command_run_id, command.source_event_id),
            )
        if command.stdout_hash:
            group_values[("command_stdout_hash", f"stdout_hash:{command.stdout_hash}")].append(
                (command.command_run_id, command.source_event_id),
            )
        group_values[("failed_command_text", f"failed_command:{command.command}")].append(
            (command.command_run_id, command.source_event_id),
        )

    for result in bundle.tool_results:
        if result.is_error is not True or not result.output_hash:
            continue
        group_values[("tool_output_hash", f"tool_output_hash:{result.output_hash}")].append(
            (result.tool_result_id, result.source_event_id)
        )

    return [
        {
            "key": key,
            "group_type": group_type,
            "record_ids": sorted(record_id for record_id, _ in records),
            "source_event_ids": sorted(
                {source_event_id for _, source_event_id in records if source_event_id}
            ),
            "repeat_count": len(records) - 1,
        }
        for (group_type, key), records in sorted(group_values.items())
        if len(records) > 1
    ]


def repeated_command_loop_failure_groups(
    groups: list[dict[str, object]],
) -> list[dict[str, object]]:
    command_loop_group_types = {
        "failed_command_text",
        "command_stdout_hash",
        "command_stderr_hash",
    }
    return [
        group
        for group in groups
        if isinstance(group.get("group_type"), str)
        and str(group["group_type"]) in command_loop_group_types
    ]


def repeated_failure_max_repeat_count(groups: list[dict[str, object]]) -> int:
    repeat_counts = [group.get("repeat_count") for group in groups]
    return max((count for count in repeat_counts if isinstance(count, int)), default=0)


def repeated_file_edit_source_events(bundle: ParsedSessionBundle) -> dict[str, list[str]]:
    source_events_by_path: defaultdict[str, set[str]] = defaultdict(set)
    edit_counts_by_path: Counter[str] = Counter()
    for activity in bundle.file_activities:
        if activity.operation not in MUTATING_FILE_OPERATIONS:
            continue
        edit_counts_by_path[activity.path] += 1
        if activity.source_event_id:
            source_events_by_path[activity.path].add(activity.source_event_id)
    return {
        path: sorted(source_events_by_path[path])
        for path, count in edit_counts_by_path.items()
        if count > 1
    }


def repeated_failure_source_event_ids(groups: list[dict[str, object]]) -> list[str]:
    source_event_ids: set[str] = set()
    for group in groups:
        group_source_event_ids = group.get("source_event_ids", [])
        if isinstance(group_source_event_ids, list):
            source_event_ids.update(
                event_id for event_id in group_source_event_ids if isinstance(event_id, str)
            )
    return sorted(source_event_ids)


def unresolved_ending_evidence(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> dict[str, object]:
    late_event_ids = ending_source_event_ids(bundle)
    event_indexes = {
        event.event_id: event.record_index
        for event in bundle.raw_events
        if event.event_id is not None
    }
    late_record_indexes = {
        event.record_index for event in bundle.raw_events if event.event_id in late_event_ids
    }
    final_answer_indexes = [
        event_indexes[message.source_event_id]
        for message in bundle.messages
        if message.role == NormalizedRole.ASSISTANT
        and message.metadata.get("phase") == "final_answer"
        and message.source_event_id in event_indexes
    ]
    late_message_event_indexes = {
        message.message_id: event_indexes.get(message.source_event_id)
        for message in bundle.messages
        if message.source_event_id in late_event_ids
    }
    late_feature_names = {
        feature.feature_name
        for feature in message_features
        if feature.message_id in late_message_event_indexes
        and feature.feature_name
        in {
            "correction_marker",
            "frustration_marker",
            "repeat_request_similarity",
        }
        and not has_later_final_answer(
            late_message_event_indexes[feature.message_id],
            final_answer_indexes,
        )
    }
    late_failed_command_ids = [
        command.command_run_id
        for command in bundle.command_runs
        if command.source_event_id in late_event_ids
        and command.exit_code is not None
        and command.exit_code != 0
        and not has_later_final_answer(
            event_indexes.get(command.source_event_id),
            final_answer_indexes,
        )
    ]
    late_warning_ids = [
        warning.warning_id
        for warning in bundle.parse_warnings
        if warning.record_index is not None
        and (
            warning.record_index in late_record_indexes
            or warning.record_index >= ending_record_index_start(bundle)
        )
        and not has_later_final_answer(warning.record_index, final_answer_indexes)
    ]
    evidence: dict[str, object] = {}
    if late_feature_names:
        evidence["late_message_features"] = sorted(late_feature_names)
    if late_failed_command_ids:
        evidence["late_failed_command_ids"] = late_failed_command_ids
    if late_warning_ids:
        evidence["late_parse_warning_ids"] = late_warning_ids
    has_late_unresolved_signal = bool(evidence)
    if not final_answer_indexes and has_late_unresolved_signal:
        evidence["missing_final_answer"] = True
    return evidence


def has_later_final_answer(
    record_index: int | None,
    final_answer_indexes: list[int],
) -> bool:
    if record_index is None:
        return False
    return any(final_answer_index > record_index for final_answer_index in final_answer_indexes)


def ending_source_event_ids(bundle: ParsedSessionBundle) -> set[str]:
    start_index = ending_record_index_start(bundle)
    event_ids = {event.event_id for event in bundle.raw_events if event.record_index >= start_index}
    event_ids.update(timestamp_window_source_event_ids(bundle))
    return event_ids


def ending_record_index_start(bundle: ParsedSessionBundle) -> int:
    if not bundle.raw_events:
        return 0
    event_count = len(bundle.raw_events)
    window_size = min(
        ENDING_WINDOW_MAX_EVENTS,
        max(ENDING_WINDOW_MIN_EVENTS, int(event_count * ENDING_WINDOW_FRACTION)),
    )
    max_index = max(event.record_index for event in bundle.raw_events)
    return max(0, max_index - window_size + 1)


def timestamp_window_source_event_ids(bundle: ParsedSessionBundle) -> set[str]:
    timestamps = [event.timestamp for event in bundle.raw_events if event.timestamp is not None]
    if not timestamps:
        return set()
    latest_timestamp = max(timestamps)
    if not isinstance(latest_timestamp, datetime):
        return set()
    cutoff = latest_timestamp - timedelta(minutes=ENDING_WINDOW_MINUTES)
    return {
        event.event_id
        for event in bundle.raw_events
        if event.timestamp is not None and event.timestamp >= cutoff
    }


def request_similarity(first: str, second: str) -> float:
    return signature_similarity(request_signature(first), request_signature(second))


def request_signature(text: str) -> RequestSignature:
    normalized = normalize_request_text(text)
    tokens = tuple(
        canonical_token(token)
        for token in normalized.split()
        if len(token) >= 2 and token not in STOPWORDS
    )
    compact_text = "".join(tokens)
    return RequestSignature(
        normalized_text=" ".join(tokens),
        tokens=tokens,
        token_set=frozenset(tokens),
        bigrams=frozenset(zip(tokens, tokens[1:], strict=False)),
        char_grams=char_grams(compact_text),
    )


def signature_similarity(first: RequestSignature, second: RequestSignature) -> float:
    if (
        len(first.tokens) < MINIMUM_COMPARABLE_TOKEN_COUNT
        or len(second.tokens) < MINIMUM_COMPARABLE_TOKEN_COUNT
    ):
        return 0.0
    score = (
        0.45 * jaccard(first.token_set, second.token_set)
        + 0.25 * jaccard(first.bigrams, second.bigrams)
        + 0.10 * jaccard(first.char_grams, second.char_grams)
        + 0.20 * salient_overlap(first.token_set, second.token_set)
    )
    if first.normalized_text == second.normalized_text:
        score += EXACT_NORMALIZED_TEXT_BOOST
    return min(score, 1.0)


def normalize_request_text(text: str) -> str:
    lowered = text.lower().replace("-", " ")
    return " ".join(re.findall(r"[a-z0-9_./]+", lowered))


def canonical_token(token: str) -> str:
    token = token.strip("./")
    if token in SYNONYMS:
        return SYNONYMS[token]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def normalized_marker_text(text: str) -> str:
    lowered = text.lower().replace("-", " ")
    return " ".join(re.findall(r"[a-z0-9_']+", lowered))


def marker_matches(text: str, marker: str) -> bool:
    normalized_marker = normalized_marker_text(marker)
    if " " in normalized_marker:
        return normalized_marker in text
    return re.search(rf"\b{re.escape(normalized_marker)}\b", text) is not None


def char_grams(text: str, size: int = 4) -> frozenset[str]:
    if len(text) < size:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[index : index + size] for index in range(len(text) - size + 1))


def jaccard(first: frozenset[object], second: frozenset[object]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def salient_overlap(first: frozenset[str], second: frozenset[str]) -> float:
    first_salient = {token for token in first if token_is_salient(token)}
    second_salient = {token for token in second if token_is_salient(token)}
    return jaccard(frozenset(first_salient), frozenset(second_salient))


def token_is_salient(token: str) -> bool:
    return (
        "_" in token
        or "/" in token
        or "." in token
        or any(character.isdigit() for character in token)
    )


def count_messages(messages: list[Message], role: NormalizedRole) -> int:
    return sum(1 for message in messages if message.role == role)


def ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def feature_evidence(features: list[MessageFeature], feature_name: str) -> dict[str, object]:
    matched_features = [feature for feature in features if feature.feature_name == feature_name]
    return {
        "message_ids": [feature.message_id for feature in matched_features],
        "source_event_ids": [
            feature.source_event_id for feature in matched_features if feature.source_event_id
        ],
    }


def message_feature_counts(features: list[MessageFeature]) -> Counter[str]:
    feature_message_ids: defaultdict[str, set[str]] = defaultdict(set)
    for feature in features:
        feature_message_ids[feature.feature_name].add(feature.message_id)
    return Counter(
        {
            feature_name: len(message_ids)
            for feature_name, message_ids in feature_message_ids.items()
        }
    )


def message_feature(
    *,
    analysis_run_id: str,
    message: Message,
    feature_name: str,
    feature_value: str,
    score: float = 1.0,
    evidence: dict[str, object] | None = None,
) -> MessageFeature:
    return MessageFeature(
        message_feature_id=stable_id(
            "message_feature",
            analysis_run_id,
            message.message_id,
            feature_name,
            feature_value,
        ),
        analysis_run_id=analysis_run_id,
        session_id=message.session_id,
        message_id=message.message_id,
        source_event_id=message.source_event_id,
        feature_name=feature_name,
        feature_value=feature_value,
        score=score,
        evidence=evidence or {},
    )


def session_feature(
    analysis_run_id: str,
    session_id: str,
    feature_name: str,
    feature_value: str | int | float | bool,
    *,
    score: float = 1.0,
    evidence: dict[str, object] | None = None,
) -> SessionFeature:
    return SessionFeature(
        session_feature_id=stable_id("session_feature", analysis_run_id, session_id, feature_name),
        analysis_run_id=analysis_run_id,
        session_id=session_id,
        feature_name=feature_name,
        feature_value=(
            str(feature_value).lower() if isinstance(feature_value, bool) else str(feature_value)
        ),
        score=score,
        evidence=evidence or {},
    )
