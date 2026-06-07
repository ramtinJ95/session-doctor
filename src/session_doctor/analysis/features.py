from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import isfinite

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    CommandRun,
    Message,
    MessageFeature,
    NormalizedRole,
    SessionFeature,
    ToolResult,
)

from . import ending as ending_helpers

REPEAT_REQUEST_SIMILARITY_THRESHOLD = 0.35
EXACT_NORMALIZED_TEXT_BOOST = 0.10
MINIMUM_COMPARABLE_TOKEN_COUNT = 4
ENDING_WINDOW_MIN_EVENTS = ending_helpers.ENDING_WINDOW_MIN_EVENTS
ENDING_WINDOW_MAX_EVENTS = ending_helpers.ENDING_WINDOW_MAX_EVENTS
ENDING_WINDOW_FRACTION = ending_helpers.ENDING_WINDOW_FRACTION
ENDING_WINDOW_MINUTES = ending_helpers.ENDING_WINDOW_MINUTES
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

AMBIGUITY_MARKERS = {
    "not sure": "unclear",
    "unclear": "unclear",
    "ambiguous": "ambiguous",
    "which one": "which_one",
    "what do you mean": "clarify",
    "can you clarify": "clarify",
}

STOP_OR_PAUSE_MARKERS = {
    "stop": "stop",
    "stop doing": "stop",
    "pause": "pause",
    "leave it": "defer",
    "never mind": "nevermind",
    "nevermind": "nevermind",
    "not now": "defer",
    "we can stop": "stop",
}
STOP_OR_PAUSE_CONTEXT_PATTERN = re.compile(r"\bstop\s+(after|before|when|once|if|at|on)\b")
SESSION_FEATURE_EVIDENCE_ALIASES = {
    "failed_command_ratio": ("failed_command_count",),
    "failed_tool_result_ratio": ("failed_tool_result_count",),
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
        ("ambiguity_marker", AMBIGUITY_MARKERS),
        ("stop_or_pause_marker", STOP_OR_PAUSE_MARKERS),
    )
    for message in messages:
        if message.role != NormalizedRole.USER or not message.text:
            continue
        text = normalized_marker_text(message.text)
        for feature_name, markers in marker_groups:
            matched_marker_families: defaultdict[str, list[str]] = defaultdict(list)
            for marker, marker_family in markers.items():
                if marker_matches_for_feature(text, feature_name, marker):
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
    context = session_feature_context(bundle, message_features)
    base_features = [
        *message_count_session_features(bundle, analysis_run_id, context),
        *command_session_features(bundle, analysis_run_id, context),
        *tool_result_session_features(bundle, analysis_run_id, context),
        *repeated_failure_session_features(analysis_run_id, context),
        *file_activity_session_features(analysis_run_id, context),
        unresolved_ending_session_feature(analysis_run_id, context),
    ]
    return [
        *base_features,
        *risk_score_session_features(analysis_run_id, context, base_features),
    ]


def session_feature_context(
    bundle: ParsedSessionBundle,
    message_features: list[MessageFeature],
) -> SessionFeatureContext:
    assert bundle.session is not None
    feature_counts = message_feature_counts(message_features)
    failed_commands = [
        command
        for command in bundle.command_runs
        if command.exit_code is not None and command.exit_code != 0
    ]
    failed_tool_results = [result for result in bundle.tool_results if result.is_error is True]
    repeated_failures = repeated_failure_groups(bundle)
    repeated_command_failures = repeated_command_loop_failure_groups(repeated_failures)
    file_edit_counts = Counter(
        activity.path
        for activity in bundle.file_activities
        if activity.operation in MUTATING_FILE_OPERATIONS
    )
    file_edit_events = file_edit_source_events(bundle)
    repeated_file_edits = {path: count for path, count in file_edit_counts.items() if count > 1}
    return SessionFeatureContext(
        session_id=bundle.session.session_id,
        message_features=message_features,
        message_feature_counts=feature_counts,
        failed_commands=failed_commands,
        failed_tool_results=failed_tool_results,
        repeated_failures=repeated_failures,
        repeated_command_failures=repeated_command_failures,
        repeated_command_failure_count=repeated_failure_max_repeat_count(
            repeated_command_failures,
        ),
        file_edit_counts=file_edit_counts,
        file_edit_events=file_edit_events,
        repeated_file_edits=repeated_file_edits,
        repeated_file_edit_events=repeated_file_edit_source_events(
            file_edit_events,
            file_edit_counts,
        ),
        unresolved_evidence=unresolved_ending_evidence(bundle, message_features),
    )


def message_count_session_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "user_message_count",
            count_messages(bundle.messages, NormalizedRole.USER),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "assistant_message_count",
            count_messages(bundle.messages, NormalizedRole.ASSISTANT),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "repeat_request_count",
            context.message_feature_counts["repeat_request_similarity"],
            evidence=feature_evidence(context.message_features, "repeat_request_similarity"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "correction_count",
            context.message_feature_counts["correction_marker"],
            evidence=feature_evidence(context.message_features, "correction_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "frustration_count",
            context.message_feature_counts["frustration_marker"],
            evidence=feature_evidence(context.message_features, "frustration_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "scope_boundary_count",
            context.message_feature_counts["scope_boundary_marker"],
            evidence=feature_evidence(context.message_features, "scope_boundary_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "ambiguity_count",
            context.message_feature_counts["ambiguity_marker"],
            evidence=feature_evidence(context.message_features, "ambiguity_marker"),
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "stop_or_pause_count",
            context.message_feature_counts["stop_or_pause_marker"],
            evidence=feature_evidence(context.message_features, "stop_or_pause_marker"),
        ),
    ]


def command_session_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    failed_command_ratio = ratio(len(context.failed_commands), len(bundle.command_runs))
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "command_count",
            len(bundle.command_runs),
            evidence={
                "command_run_ids": [command.command_run_id for command in bundle.command_runs],
                "source_event_ids": [
                    command.source_event_id
                    for command in bundle.command_runs
                    if command.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_command_count",
            len(context.failed_commands),
            evidence={
                "command_run_ids": [command.command_run_id for command in context.failed_commands],
                "source_event_ids": [
                    command.source_event_id
                    for command in context.failed_commands
                    if command.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_command_ratio",
            failed_command_ratio,
            score=failed_command_ratio,
        ),
    ]


def tool_result_session_features(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    failed_tool_result_ratio = ratio(len(context.failed_tool_results), len(bundle.tool_results))
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "tool_result_count",
            len(bundle.tool_results),
            evidence={
                "tool_result_ids": [result.tool_result_id for result in bundle.tool_results],
                "source_event_ids": [
                    result.source_event_id
                    for result in bundle.tool_results
                    if result.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_tool_result_count",
            len(context.failed_tool_results),
            evidence={
                "tool_result_ids": [
                    result.tool_result_id for result in context.failed_tool_results
                ],
                "source_event_ids": [
                    result.source_event_id
                    for result in context.failed_tool_results
                    if result.source_event_id
                ],
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "failed_tool_result_ratio",
            failed_tool_result_ratio,
            score=failed_tool_result_ratio,
        ),
    ]


def repeated_failure_session_features(
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "repeated_failure_count",
            sum(group["repeat_count"] for group in context.repeated_failures),
            evidence={
                "groups": context.repeated_failures,
                "source_event_ids": repeated_failure_source_event_ids(context.repeated_failures),
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "repeated_command_failure_count",
            context.repeated_command_failure_count,
            evidence={
                "groups": context.repeated_command_failures,
                "source_event_ids": repeated_failure_source_event_ids(
                    context.repeated_command_failures
                ),
            },
        ),
    ]


def file_activity_session_features(
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> list[SessionFeature]:
    return [
        session_feature(
            analysis_run_id,
            context.session_id,
            "edited_file_count",
            len(context.file_edit_counts),
            evidence={
                "paths": sorted(context.file_edit_counts),
                "source_event_ids_by_path": context.file_edit_events,
                "source_event_ids": sorted(
                    {
                        source_event_id
                        for source_event_ids in context.file_edit_events.values()
                        for source_event_id in source_event_ids
                    }
                ),
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "same_file_edited_repeatedly_count",
            len(context.repeated_file_edits),
            evidence={
                "paths": context.repeated_file_edits,
                "source_event_ids_by_path": context.repeated_file_edit_events,
                "source_event_ids": sorted(
                    {
                        source_event_id
                        for source_event_ids in context.repeated_file_edit_events.values()
                        for source_event_id in source_event_ids
                    }
                ),
            },
        ),
        session_feature(
            analysis_run_id,
            context.session_id,
            "max_edits_to_single_file",
            max(context.file_edit_counts.values(), default=0),
            evidence=max_file_edit_evidence(context),
        ),
    ]


def unresolved_ending_session_feature(
    analysis_run_id: str,
    context: SessionFeatureContext,
) -> SessionFeature:
    return session_feature(
        analysis_run_id,
        context.session_id,
        "unresolved_ending_signal",
        bool(context.unresolved_evidence),
        score=1.0 if context.unresolved_evidence else 0.0,
        evidence=context.unresolved_evidence,
    )


def risk_score_session_features(
    analysis_run_id: str,
    context: SessionFeatureContext,
    base_features: list[SessionFeature],
) -> list[SessionFeature]:
    features_by_name = {feature.feature_name: feature for feature in base_features}
    return [
        risk_score_feature(
            analysis_run_id=analysis_run_id,
            context=context,
            feature_name="friction_score",
            formula_version="friction_score_v1",
            base_features=features_by_name,
            component_values={
                "frustration_count": capped_count(
                    int_session_feature(features_by_name, "frustration_count"), cap=3
                ),
                "correction_count": capped_count(
                    int_session_feature(features_by_name, "correction_count"), cap=3
                ),
                "failed_command_ratio": float_session_feature(
                    features_by_name, "failed_command_ratio"
                ),
                "failed_tool_result_ratio": float_session_feature(
                    features_by_name, "failed_tool_result_ratio"
                ),
                "repeated_failure_count": capped_count(
                    int_session_feature(features_by_name, "repeated_failure_count"), cap=3
                ),
                "unresolved_ending_signal": 1.0
                if bool_session_feature(features_by_name, "unresolved_ending_signal")
                else 0.0,
            },
            component_weights={
                "frustration_count": 0.18,
                "correction_count": 0.14,
                "failed_command_ratio": 0.22,
                "failed_tool_result_ratio": 0.18,
                "repeated_failure_count": 0.14,
                "unresolved_ending_signal": 0.18,
            },
        ),
        risk_score_feature(
            analysis_run_id=analysis_run_id,
            context=context,
            feature_name="stuckness_score",
            formula_version="stuckness_score_v1",
            base_features=features_by_name,
            component_values={
                "repeat_request_count": capped_count(
                    int_session_feature(features_by_name, "repeat_request_count"), cap=3
                ),
                "correction_count": capped_count(
                    int_session_feature(features_by_name, "correction_count"), cap=3
                ),
                "frustration_count": capped_count(
                    int_session_feature(features_by_name, "frustration_count"), cap=3
                ),
                "repeated_command_failure_count": capped_count(
                    int_session_feature(features_by_name, "repeated_command_failure_count"), cap=3
                ),
                "same_file_edited_repeatedly_count": capped_count(
                    int_session_feature(features_by_name, "same_file_edited_repeatedly_count"),
                    cap=3,
                ),
                "unresolved_ending_signal": 1.0
                if bool_session_feature(features_by_name, "unresolved_ending_signal")
                else 0.0,
            },
            component_weights={
                "repeat_request_count": 0.22,
                "correction_count": 0.20,
                "frustration_count": 0.12,
                "repeated_command_failure_count": 0.20,
                "same_file_edited_repeatedly_count": 0.10,
                "unresolved_ending_signal": 0.20,
            },
        ),
        risk_score_feature(
            analysis_run_id=analysis_run_id,
            context=context,
            feature_name="prompt_clarity_risk",
            formula_version="prompt_clarity_risk_v1",
            base_features=features_by_name,
            component_values={
                "scope_boundary_count": capped_count(
                    int_session_feature(features_by_name, "scope_boundary_count"), cap=4
                ),
                "correction_count": capped_count(
                    int_session_feature(features_by_name, "correction_count"), cap=3
                ),
                "repeat_request_count": capped_count(
                    int_session_feature(features_by_name, "repeat_request_count"), cap=3
                ),
                "ambiguity_count": capped_count(
                    int_session_feature(features_by_name, "ambiguity_count"), cap=3
                ),
            },
            component_weights={
                "scope_boundary_count": 0.22,
                "correction_count": 0.24,
                "repeat_request_count": 0.20,
                "ambiguity_count": 0.18,
            },
        ),
        risk_score_feature(
            analysis_run_id=analysis_run_id,
            context=context,
            feature_name="agent_fit_risk",
            formula_version="agent_fit_risk_v1",
            base_features=features_by_name,
            component_values={
                "failed_command_ratio": float_session_feature(
                    features_by_name, "failed_command_ratio"
                ),
                "failed_tool_result_ratio": float_session_feature(
                    features_by_name, "failed_tool_result_ratio"
                ),
                "repeated_command_failure_count": capped_count(
                    int_session_feature(features_by_name, "repeated_command_failure_count"), cap=3
                ),
                "same_file_edited_repeatedly_count": capped_count(
                    int_session_feature(features_by_name, "same_file_edited_repeatedly_count"),
                    cap=3,
                ),
                "edited_file_count": capped_count(
                    int_session_feature(features_by_name, "edited_file_count"), cap=8
                ),
                "unresolved_ending_signal": 1.0
                if bool_session_feature(features_by_name, "unresolved_ending_signal")
                else 0.0,
            },
            component_weights={
                "failed_command_ratio": 0.20,
                "failed_tool_result_ratio": 0.18,
                "repeated_command_failure_count": 0.24,
                "same_file_edited_repeatedly_count": 0.12,
                "edited_file_count": 0.10,
                "unresolved_ending_signal": 0.18,
            },
        ),
        risk_score_feature(
            analysis_run_id=analysis_run_id,
            context=context,
            feature_name="project_complexity_signal",
            formula_version="project_complexity_signal_v1",
            base_features=features_by_name,
            component_values={
                "edited_file_count": capped_count(
                    int_session_feature(features_by_name, "edited_file_count"), cap=8
                ),
                "same_file_edited_repeatedly_count": capped_count(
                    int_session_feature(features_by_name, "same_file_edited_repeatedly_count"),
                    cap=4,
                ),
                "max_edits_to_single_file": capped_count(
                    int_session_feature(features_by_name, "max_edits_to_single_file"), cap=6
                ),
                "command_count": capped_count(
                    int_session_feature(features_by_name, "command_count"), cap=12
                ),
                "tool_result_count": capped_count(
                    int_session_feature(features_by_name, "tool_result_count"), cap=20
                ),
            },
            component_weights={
                "edited_file_count": 0.22,
                "same_file_edited_repeatedly_count": 0.18,
                "max_edits_to_single_file": 0.16,
                "command_count": 0.16,
                "tool_result_count": 0.12,
            },
        ),
    ]


def risk_score_feature(
    *,
    analysis_run_id: str,
    context: SessionFeatureContext,
    feature_name: str,
    formula_version: str,
    base_features: dict[str, SessionFeature],
    component_values: dict[str, float],
    component_weights: dict[str, float],
) -> SessionFeature:
    contributions = {
        name: component_values[name] * component_weights[name]
        for name in component_weights
        if name in component_values
    }
    score = clamp01(sum(contributions.values()))
    contributing_features = sorted(component_values)
    return session_feature(
        analysis_run_id,
        context.session_id,
        feature_name,
        score_feature_value(score),
        score=score,
        evidence={
            "contributing_features": contributing_features,
            "source_event_ids": source_event_ids_for_session_features(
                base_features,
                contributing_features,
            ),
        },
        metadata={
            "formula": formula_version,
            "component_values": round_score_mapping(component_values),
            "component_weights": component_weights,
            "contributions": round_score_mapping(contributions),
        },
    )


def source_event_ids_for_session_features(
    features: dict[str, SessionFeature],
    feature_names: list[str],
) -> list[str]:
    source_event_ids: set[str] = set()
    for feature_name in feature_names:
        for evidence_feature_name in evidence_feature_names(feature_name):
            feature = features.get(evidence_feature_name)
            if feature is None:
                continue
            raw_source_event_ids = feature.evidence.get("source_event_ids", [])
            if isinstance(raw_source_event_ids, list):
                source_event_ids.update(
                    event_id for event_id in raw_source_event_ids if isinstance(event_id, str)
                )
    return sorted(source_event_ids)


def evidence_feature_names(feature_name: str) -> tuple[str, ...]:
    return (feature_name, *SESSION_FEATURE_EVIDENCE_ALIASES.get(feature_name, ()))


def round_score_mapping(values: dict[str, float]) -> dict[str, float]:
    return {name: round(value, 3) for name, value in sorted(values.items())}


def int_session_feature(features: dict[str, SessionFeature], name: str) -> int:
    try:
        return int(float(features[name].feature_value))
    except (KeyError, ValueError):
        return 0


def float_session_feature(features: dict[str, SessionFeature], name: str) -> float:
    try:
        return float(features[name].feature_value)
    except (KeyError, ValueError):
        return 0.0


def bool_session_feature(features: dict[str, SessionFeature], name: str) -> bool:
    feature = features.get(name)
    return feature is not None and feature.feature_value == "true"


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


def max_file_edit_evidence(context: SessionFeatureContext) -> dict[str, object]:
    max_edit_count = max(context.file_edit_counts.values(), default=0)
    if max_edit_count == 0:
        return {"paths": [], "source_event_ids_by_path": {}, "source_event_ids": []}
    max_edit_paths = sorted(
        path for path, count in context.file_edit_counts.items() if count == max_edit_count
    )
    source_event_ids_by_path = {
        path: context.file_edit_events.get(path, []) for path in max_edit_paths
    }
    return {
        "paths": max_edit_paths,
        "source_event_ids_by_path": source_event_ids_by_path,
        "source_event_ids": sorted(
            {
                source_event_id
                for source_event_ids in source_event_ids_by_path.values()
                for source_event_id in source_event_ids
            }
        ),
    }


def file_edit_source_events(bundle: ParsedSessionBundle) -> dict[str, list[str]]:
    source_events_by_path: defaultdict[str, set[str]] = defaultdict(set)
    for activity in bundle.file_activities:
        if activity.operation not in MUTATING_FILE_OPERATIONS:
            continue
        if activity.source_event_id:
            source_events_by_path[activity.path].add(activity.source_event_id)
    return {
        path: sorted(source_event_ids)
        for path, source_event_ids in sorted(source_events_by_path.items())
    }


def repeated_file_edit_source_events(
    file_edit_events: dict[str, list[str]],
    file_edit_counts: Counter[str],
) -> dict[str, list[str]]:
    return {
        path: file_edit_events.get(path, [])
        for path, count in file_edit_counts.items()
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
    return ending_helpers.unresolved_ending_evidence(bundle, message_features)


def has_later_final_answer(
    record_index: int | None,
    final_answer_indexes: list[int],
) -> bool:
    return ending_helpers.has_later_final_answer(record_index, final_answer_indexes)


def ending_source_event_ids(bundle: ParsedSessionBundle) -> set[str]:
    return ending_helpers.ending_source_event_ids(bundle)


def ending_record_index_start(bundle: ParsedSessionBundle) -> int:
    return ending_helpers.ending_record_index_start(bundle)


def timestamp_window_source_event_ids(bundle: ParsedSessionBundle) -> set[str]:
    return ending_helpers.timestamp_window_source_event_ids(bundle)


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


def marker_matches_for_feature(text: str, feature_name: str, marker: str) -> bool:
    if feature_name == "stop_or_pause_marker" and marker == "stop":
        return marker_matches(text, marker) and not STOP_OR_PAUSE_CONTEXT_PATTERN.search(text)
    return marker_matches(text, marker)


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


def clamp01(value: float) -> float:
    if not isfinite(value):
        msg = "score value must be finite"
        raise ValueError(msg)
    return max(0.0, min(1.0, value))


def capped_count(value: int, *, cap: int) -> float:
    if cap <= 0:
        msg = "cap must be greater than zero"
        raise ValueError(msg)
    return clamp01(max(value, 0) / cap)


def score_feature_value(score: float) -> str:
    return f"{clamp01(score):.3f}"


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
    metadata: dict[str, object] | None = None,
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
        metadata=metadata or {},
    )


def session_feature(
    analysis_run_id: str,
    session_id: str,
    feature_name: str,
    feature_value: str | int | float | bool,
    *,
    score: float = 1.0,
    evidence: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
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
        metadata=metadata or {},
    )
