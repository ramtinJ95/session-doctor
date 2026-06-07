from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import analyze_features, classify_session
from session_doctor.schemas import AgentName, NormalizedRole, RawEvent, Session, ToolResult

from .fixtures import (
    analysis_fixture_bundle,
    broad_low_friction_bundle,
    clean_finished_bundle,
    complex_high_friction_bundle,
    message,
    prompt_ambiguous_bundle,
    prompt_ambiguous_with_tool_failure_bundle,
    resolved_after_correction_bundle,
    tooling_loop_without_user_stuck_bundle,
)


def test_classify_session_emits_initial_deterministic_labels() -> None:
    bundle = analysis_fixture_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert {"user_stuck", "tooling_blocked"}.issubset(labels)
    assert "agent_looping" not in labels
    assert "resolved_after_corrections" not in labels
    tooling_blocked = next(
        classification
        for classification in classifications
        if classification.label == "tooling_blocked"
    )
    assert tooling_blocked.evidence_event_ids
    assert tooling_blocked.metadata["rule"] == "tooling_blocked_v2"
    assert tooling_blocked.metadata["score_feature"] == "friction_score"
    assert tooling_blocked.metadata["threshold"] == 0.5
    assert tooling_blocked.evidence_summary.startswith("Session has tooling blocker evidence")

    user_stuck = next(
        classification for classification in classifications if classification.label == "user_stuck"
    )
    assert user_stuck.metadata["rule"] == "user_stuck_v2"
    assert user_stuck.metadata["score_feature"] == "stuckness_score"
    assert user_stuck.metadata["contributing_features"] == [
        "repeat_request_count",
        "correction_count",
        "frustration_count",
        "repeated_command_failure_count",
        "same_file_edited_repeatedly_count",
        "unresolved_ending_signal",
    ]
    assert "repeated user request" in user_stuck.evidence_summary


def test_classify_session_detects_resolution_after_correction() -> None:
    bundle = resolved_after_correction_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "resolved_after_corrections" in labels
    assert "user_stuck" not in labels
    assert "agent_misunderstood" in labels
    resolved = next(
        classification
        for classification in classifications
        if classification.label == "resolved_after_corrections"
    )
    assert "score_feature" not in resolved.metadata
    assert resolved.metadata["fixed_score"] == 0.7


def test_classify_session_emits_healthy_for_clean_finished_session() -> None:
    bundle = clean_finished_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    assert [classification.label for classification in classifications] == ["healthy"]
    healthy = classifications[0]
    assert healthy.metadata["rule"] == "healthy_v1"
    assert healthy.evidence_summary.startswith("Session appears clean")


def test_classify_session_does_not_emit_healthy_for_unfinished_user_only_session() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id="event-1",
                source_id="source-1",
                agent_name=AgentName.CODEX,
                record_index=1,
            )
        ],
        messages=[
            message(
                "message-1",
                NormalizedRole.USER,
                "Please summarize the repository.",
                "event-1",
            )
        ],
    )
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    assert "healthy" not in {classification.label for classification in classifications}


def test_classify_session_emits_tooling_blocked_for_failed_tool_ratio() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id="event-1",
                source_id="source-1",
                agent_name=AgentName.CODEX,
                record_index=1,
            )
        ],
        tool_results=[
            ToolResult(
                tool_result_id="tool-result-1",
                session_id=session.session_id,
                source_event_id="event-1",
                is_error=True,
            )
        ],
    )
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    tooling_blocked = next(
        classification
        for classification in classifications
        if classification.label == "tooling_blocked"
    )
    assert tooling_blocked.evidence_event_ids == ["event-1"]
    session_features = {feature.feature_name: feature for feature in features.session_features}
    assert session_features["friction_score"].evidence["source_event_ids"] == ["event-1"]


def test_classify_session_emits_prompt_ambiguous_from_calibrated_markers() -> None:
    bundle = prompt_ambiguous_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "prompt_ambiguous" in labels
    session_features = {feature.feature_name: feature for feature in features.session_features}
    assert session_features["ambiguity_count"].feature_value == "4"
    assert session_features["prompt_clarity_risk"].score >= 0.55


def test_classify_session_suppresses_prompt_ambiguous_for_failed_tooling() -> None:
    bundle = prompt_ambiguous_with_tool_failure_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "tooling_blocked" in labels
    assert "prompt_ambiguous" not in labels


def test_classify_session_avoids_prompt_ambiguous_for_single_scope_boundary() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    bundle = ParsedSessionBundle(
        session=session,
        messages=[message("message-1", NormalizedRole.USER, "Only update README.md.", "event-1")],
    )
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    assert "prompt_ambiguous" not in {classification.label for classification in classifications}


def test_classify_session_emits_size_and_complexity_labels_conservatively() -> None:
    low_friction_features = analyze_features(broad_low_friction_bundle(), "analysis-1")
    low_friction_classifications = classify_session(
        broad_low_friction_bundle(),
        "analysis-1",
        low_friction_features.message_features,
        low_friction_features.session_features,
    )

    low_friction_labels = {classification.label for classification in low_friction_classifications}
    assert "task_too_large" not in low_friction_labels

    bundle = complex_high_friction_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-2")
    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-2",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "task_too_large" in labels
    assert "repo_complexity_high" in labels
    task_too_large = next(
        classification
        for classification in classifications
        if classification.label == "task_too_large"
    )
    assert "unresolved_ending_signal" in task_too_large.metadata["contributing_features"]
    assert task_too_large.metadata["extra_thresholds"] == {
        "friction_score": 0.35,
        "unresolved_ending_signal": 1,
        "broad_surface_edited_file_count": 6,
        "broad_surface_command_count": 8,
    }


def test_user_stuck_requires_user_facing_stuck_evidence() -> None:
    bundle = tooling_loop_without_user_stuck_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "tooling_blocked" in labels
    assert "agent_looping" in labels
    assert "user_stuck" not in labels
