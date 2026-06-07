from __future__ import annotations

from math import isfinite

from session_doctor.schemas import SessionFeature

from .feature_factories import session_feature
from .feature_models import SessionFeatureContext

SESSION_FEATURE_EVIDENCE_ALIASES = {
    "failed_command_ratio": ("failed_command_count",),
    "failed_tool_result_ratio": ("failed_tool_result_count",),
}


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
