from __future__ import annotations

import re
from collections import defaultdict

from session_doctor.schemas import Message, MessageFeature, NormalizedRole

from .feature_factories import message_feature

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
