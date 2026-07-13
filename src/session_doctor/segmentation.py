from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.ids import stable_id
from session_doctor.schemas import (
    BoundaryDecision,
    BoundaryReason,
    EpisodeAnalysis,
    EpisodeBoundary,
    EpisodeObservation,
    Message,
    NormalizedRole,
    TaskEpisode,
)
from session_doctor.store.lifecycle import FINALIZED_LIFECYCLE_STATES, LifecycleObservation

SEGMENTATION_VERSION = "segmentation-v1"

EXPLICIT_NEW_TASK = re.compile(
    r"^\s*(?:new|separate|unrelated)\s+(?:task|request)\s*[:\-]|"
    r"^\s*(?:switching|moving)\s+to\s+(?:a\s+)?(?:new|different)\s+(?:task|topic)\b",
    re.IGNORECASE,
)
CORRECTION_OR_CONTINUATION = re.compile(
    r"^\s*(?:actually|correction|to clarify|i mean|no[,—-]|please continue|continue|"
    r"review (?:that|the)|try again|fix (?:that|it))\b",
    re.IGNORECASE,
)
WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "please",
    "the",
    "to",
    "with",
}


def segment_session(
    bundle: ParsedSessionBundle,
    lifecycle: LifecycleObservation,
) -> EpisodeAnalysis:
    if bundle.session is None:
        raise ValueError("episode segmentation requires a native session")
    user_messages = [message for message in bundle.messages if message.role is NormalizedRole.USER]
    boundaries = [
        classify_boundary(bundle, left, right)
        for left, right in zip(user_messages, user_messages[1:], strict=False)
    ]
    groups: list[list[Message]] = []
    current: list[Message] = []
    for index, message in enumerate(user_messages):
        current.append(message)
        if index < len(boundaries) and boundaries[index].decision is BoundaryDecision.SPLIT:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    provisional = lifecycle.state not in FINALIZED_LIFECYCLE_STATES
    episodes: list[TaskEpisode] = []
    observations: list[EpisodeObservation] = []
    boundary_by_left = {row.left_user_anchor_id: row for row in boundaries}
    for group in groups:
        anchors = [message_anchor(message) for message in group]
        episode_id = stable_id(
            "task-episode",
            SEGMENTATION_VERSION,
            bundle.session.session_id,
            anchors[0],
            anchors[-1],
        )
        episode_boundaries = [
            boundary_by_left[anchor].boundary_id for anchor in anchors if anchor in boundary_by_left
        ]
        event_anchors = ordered_unique(
            message_anchor(message)
            for message in messages_between(bundle.messages, group[0], group[-1])
        )
        episode = TaskEpisode(
            episode_id=episode_id,
            segmentation_version=SEGMENTATION_VERSION,
            session_id=bundle.session.session_id,
            first_user_anchor_id=anchors[0],
            last_user_anchor_id=anchors[-1],
            user_anchor_ids=anchors,
            event_anchor_ids=event_anchors or anchors,
            boundary_ids=episode_boundaries,
            lifecycle_state=lifecycle.state,
            provisional=provisional,
        )
        episodes.append(episode)
        for boundary in boundaries:
            if boundary.left_user_anchor_id not in anchors:
                continue
            if boundary.reason is BoundaryReason.EXPLICIT_NEW_TASK:
                observations.append(
                    EpisodeObservation(
                        observation_id=stable_id(
                            "episode-observation", episode_id, boundary.boundary_id
                        ),
                        episode_id=episode_id,
                        observation_kind="interrupted_unknown_by_explicit_replacement",
                        evidence_anchor_ids=boundary.evidence_anchor_ids,
                    )
                )
            elif boundary.decision is BoundaryDecision.AMBIGUOUS:
                observations.append(
                    EpisodeObservation(
                        observation_id=stable_id(
                            "episode-observation", episode_id, boundary.boundary_id
                        ),
                        episode_id=episode_id,
                        observation_kind="ambiguous_boundary_merged",
                        evidence_anchor_ids=boundary.evidence_anchor_ids,
                    )
                )
    return EpisodeAnalysis(
        segmentation_version=SEGMENTATION_VERSION,
        session_id=bundle.session.session_id,
        lifecycle_observation_id=lifecycle.lifecycle_observation_id,
        lifecycle_state=lifecycle.state,
        episodes=episodes,
        boundaries=boundaries,
        observations=observations,
    )


def classify_boundary(
    bundle: ParsedSessionBundle,
    left: Message,
    right: Message,
) -> EpisodeBoundary:
    left_anchor = message_anchor(left)
    right_anchor = message_anchor(right)
    right_text = right.text or ""
    similarity = broad_goal_similarity(left.text or "", right_text)
    if EXPLICIT_NEW_TASK.search(right_text):
        decision = BoundaryDecision.SPLIT
        reason = BoundaryReason.EXPLICIT_NEW_TASK
    elif CORRECTION_OR_CONTINUATION.search(right_text) or similarity >= 0.62:
        decision = BoundaryDecision.NO_SPLIT
        reason = BoundaryReason.CORRECTION_OR_REPEAT
    elif interaction_closed_between(bundle.messages, left, right) and similarity <= 0.12:
        decision = BoundaryDecision.SPLIT
        reason = BoundaryReason.CLOSURE_AND_TOPIC_SHIFT
    else:
        decision = BoundaryDecision.AMBIGUOUS
        reason = BoundaryReason.WEAK_OR_CONFLICTING
    return EpisodeBoundary(
        boundary_id=stable_id("episode-boundary", SEGMENTATION_VERSION, left_anchor, right_anchor),
        segmentation_version=SEGMENTATION_VERSION,
        session_id=left.session_id,
        left_user_anchor_id=left_anchor,
        right_user_anchor_id=right_anchor,
        decision=decision,
        reason=reason,
        evidence_anchor_ids=[left_anchor, right_anchor],
        broad_goal_similarity=similarity,
    )


def broad_goal_similarity(left: str, right: str) -> float:
    left_terms = goal_terms(left)
    right_terms = goal_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def goal_terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return {term for term in WORD.findall(normalized) if term not in STOPWORDS}


def interaction_closed_between(
    messages: list[Message],
    left: Message,
    right: Message,
) -> bool:
    rows = messages_between(messages, left, right)[1:-1]
    assistants = [row for row in rows if row.role is NormalizedRole.ASSISTANT]
    if not assistants:
        return False
    last = assistants[-1]
    phase = last.metadata.get("phase")
    return phase in {"final_answer", "final"} or bool(last.metadata.get("turn_closed"))


def messages_between(messages: list[Message], left: Message, right: Message) -> list[Message]:
    left_index = messages.index(left)
    right_index = messages.index(right)
    return messages[left_index : right_index + 1]


def message_anchor(message: Message) -> str:
    return message.source_event_id or message.message_id


def ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
