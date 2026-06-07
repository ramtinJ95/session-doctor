from __future__ import annotations

import pytest

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import (
    REPEAT_REQUEST_SIMILARITY_THRESHOLD,
    analyze_features,
    classify_session,
)
from session_doctor.schemas import AgentName, FileActivity, NormalizedRole, Session

from .fixtures import (
    analysis_fixture_bundle,
    broad_low_friction_bundle,
    clean_finished_bundle,
    message,
)


def test_analyze_features_detects_message_and_session_signals() -> None:
    bundle = analysis_fixture_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    message_feature_names = {feature.feature_name for feature in result.message_features}
    assert {
        "repeat_request_similarity",
        "correction_marker",
        "frustration_marker",
        "scope_boundary_marker",
    }.issubset(message_feature_names)

    session_features = {feature.feature_name: feature for feature in result.session_features}
    assert session_features["repeat_request_count"].feature_value == "1"
    assert session_features["correction_count"].feature_value == "1"
    assert session_features["frustration_count"].feature_value == "1"
    assert session_features["scope_boundary_count"].feature_value == "1"
    assert session_features["command_count"].feature_value == "2"
    assert session_features["failed_command_count"].feature_value == "2"
    assert session_features["failed_command_ratio"].feature_value == "1.0"
    assert session_features["failed_tool_result_count"].feature_value == "1"
    assert session_features["repeated_failure_count"].feature_value == "2"
    assert session_features["repeated_command_failure_count"].feature_value == "1"
    assert session_features["same_file_edited_repeatedly_count"].feature_value == "1"
    assert session_features["max_edits_to_single_file"].feature_value == "2"
    assert session_features["unresolved_ending_signal"].feature_value == "true"
    repeat_request = next(
        feature
        for feature in result.message_features
        if feature.feature_name == "repeat_request_similarity"
    )
    assert repeat_request.evidence["matched_message_id"] == "message-1"
    assert repeat_request.evidence["matched_source_event_id"] == "event-1"
    assert repeat_request.evidence["threshold"] == REPEAT_REQUEST_SIMILARITY_THRESHOLD
    assert isinstance(repeat_request.evidence["similarity_score"], float)
    repeated_failure_groups = session_features["repeated_failure_count"].evidence["groups"]
    assert {
        group["group_type"] for group in repeated_failure_groups if isinstance(group, dict)
    } == {"command_stdout_hash", "failed_command_text"}


def test_analyze_features_emits_phase6_risk_scores_with_metadata() -> None:
    bundle = analysis_fixture_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    expected_score_names = {
        "friction_score",
        "stuckness_score",
        "prompt_clarity_risk",
        "agent_fit_risk",
        "project_complexity_signal",
    }
    assert expected_score_names.issubset(session_features)
    assert session_features["friction_score"].feature_value == "0.780"
    assert session_features["friction_score"].score == pytest.approx(0.78)
    assert session_features["stuckness_score"].feature_value == "0.480"
    assert session_features["friction_score"].metadata["formula"] == "friction_score_v1"
    assert session_features["friction_score"].metadata["component_values"] == {
        "correction_count": 0.333,
        "failed_command_ratio": 1.0,
        "failed_tool_result_ratio": 1.0,
        "frustration_count": 0.333,
        "repeated_failure_count": 0.667,
        "unresolved_ending_signal": 1.0,
    }
    assert "event-10" in session_features["friction_score"].evidence["source_event_ids"]


def test_risk_scores_distinguish_clean_and_complex_sessions() -> None:
    clean_result = analyze_features(clean_finished_bundle(), analysis_run_id="analysis-1")
    complex_result = analyze_features(broad_low_friction_bundle(), analysis_run_id="analysis-2")

    clean_features = {feature.feature_name: feature for feature in clean_result.session_features}
    complex_features = {
        feature.feature_name: feature for feature in complex_result.session_features
    }

    assert clean_features["friction_score"].feature_value == "0.000"
    assert clean_features["stuckness_score"].feature_value == "0.000"
    assert clean_features["agent_fit_risk"].score < 0.25
    assert complex_features["project_complexity_signal"].score >= 0.65
    assert complex_features["friction_score"].score < 0.25
    assert "event-29" in complex_features["project_complexity_signal"].evidence["source_event_ids"]
    assert "event-2" in complex_features["edited_file_count"].evidence["source_event_ids"]
    assert "event-29" in complex_features["tool_result_count"].evidence["source_event_ids"]
    assert "event-13" in complex_features["command_count"].evidence["source_event_ids"]
    assert "event-13" in complex_features["max_edits_to_single_file"].evidence["source_event_ids"]


def test_marker_features_deduplicate_same_family_per_message() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    bundle = ParsedSessionBundle(
        session=session,
        messages=[
            message(
                "message-1",
                NormalizedRole.USER,
                "Be thorough, this is very important. Don't do not change more scope.",
                "event-1",
            )
        ],
    )

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    marker_pairs = [
        (feature.feature_name, feature.feature_value)
        for feature in result.message_features
        if feature.feature_name in {"frustration_marker", "scope_boundary_marker"}
    ]
    assert marker_pairs.count(("frustration_marker", "high_stakes")) == 1
    assert marker_pairs.count(("scope_boundary_marker", "do_not")) == 1
    high_stakes_feature = next(
        feature
        for feature in result.message_features
        if feature.feature_name == "frustration_marker" and feature.feature_value == "high_stakes"
    )
    assert high_stakes_feature.evidence == {"matched_markers": ["be thorough", "very important"]}
    assert len({feature.message_feature_id for feature in result.message_features}) == len(
        result.message_features
    )


def test_file_edit_features_ignore_repeated_reads_and_count_patches() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.PI,
    )
    bundle = ParsedSessionBundle(
        session=session,
        file_activities=[
            FileActivity(
                file_activity_id="read-1",
                session_id=session.session_id,
                path="README.md",
                operation="read",
            ),
            FileActivity(
                file_activity_id="read-2",
                session_id=session.session_id,
                path="README.md",
                operation="read",
            ),
            FileActivity(
                file_activity_id="write-1",
                session_id=session.session_id,
                source_event_id="event-1",
                path="scratch/output.txt",
                operation="write",
            ),
            FileActivity(
                file_activity_id="patch-1",
                session_id=session.session_id,
                source_event_id="event-2",
                path="README.md",
                operation="patch",
            ),
            FileActivity(
                file_activity_id="patch-2",
                session_id=session.session_id,
                source_event_id="event-3",
                path="README.md",
                operation="patch",
            ),
        ],
    )

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    assert session_features["edited_file_count"].feature_value == "2"
    assert session_features["same_file_edited_repeatedly_count"].feature_value == "1"
    assert session_features["max_edits_to_single_file"].feature_value == "2"
    assert session_features["same_file_edited_repeatedly_count"].evidence == {
        "paths": {"README.md": 2},
        "source_event_ids": ["event-2", "event-3"],
        "source_event_ids_by_path": {"README.md": ["event-2", "event-3"]},
    }


def test_scope_boundary_phrase_does_not_count_as_correction() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    bundle = ParsedSessionBundle(
        session=session,
        messages=[
            message(
                "message-1",
                NormalizedRole.USER,
                "No need to do code changes yet.",
                "event-1",
            )
        ],
    )

    result = analyze_features(bundle, analysis_run_id="analysis-1")
    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=result.message_features,
        session_features=result.session_features,
    )

    session_features = {feature.feature_name: feature for feature in result.session_features}
    assert session_features["scope_boundary_count"].feature_value == "1"
    assert session_features["correction_count"].feature_value == "0"
    assert "user_stuck" not in {classification.label for classification in classifications}
