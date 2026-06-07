from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.schemas import (
    MessageFeature,
    SessionClassification,
    SessionFeature,
)

from .classification_context import ClassificationContext
from .classification_rules import (
    abandoned_or_stopped_classification,
    agent_looping_classification,
    agent_misunderstood_classification,
    healthy_classification,
    prompt_ambiguous_classification,
    repo_complexity_high_classification,
    resolved_after_corrections_classification,
    task_too_large_classification,
    tooling_blocked_classification,
    user_stuck_classification,
)

__all__ = ["classify_session"]


def classify_session(
    bundle: ParsedSessionBundle,
    analysis_run_id: str,
    message_features: list[MessageFeature],
    session_features: list[SessionFeature],
) -> list[SessionClassification]:
    if bundle.session is None:
        msg = "Cannot classify a bundle without a session record."
        raise ValueError(msg)

    context = ClassificationContext(
        bundle=bundle,
        analysis_run_id=analysis_run_id,
        message_features=message_features,
        session_features={feature.feature_name: feature for feature in session_features},
    )
    label_rules = (
        user_stuck_classification,
        tooling_blocked_classification,
        agent_looping_classification,
        resolved_after_corrections_classification,
        agent_misunderstood_classification,
        prompt_ambiguous_classification,
        task_too_large_classification,
        repo_complexity_high_classification,
        abandoned_or_stopped_classification,
    )
    classifications = [
        classification for rule in label_rules if (classification := rule(context)) is not None
    ]
    if healthy := healthy_classification(context, classifications):
        classifications.append(healthy)
    return classifications
