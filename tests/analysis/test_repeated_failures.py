from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import analyze_features, classify_session
from session_doctor.schemas import AgentName, FileActivity, Session

from .fixtures import (
    mixed_small_command_and_tool_failure_groups_bundle,
    repeated_command_failure_bundle,
    repeated_tool_result_failure_bundle,
    shared_stderr_distinct_command_failure_bundle,
)


def test_agent_looping_repeated_failure_evidence_includes_event_ids() -> None:
    bundle = repeated_command_failure_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    agent_looping = next(
        classification
        for classification in classifications
        if classification.label == "agent_looping"
    )
    assert agent_looping.evidence_event_ids == ["event-1", "event-2", "event-3"]
    assert "threshold" not in agent_looping.metadata
    assert agent_looping.metadata["extra_thresholds"] == {
        "repeat_request_count": 2,
        "same_file_edited_repeatedly_count": 1,
        "repeated_command_failure_count": 2,
    }


def test_agent_looping_ignores_non_command_repeated_failures() -> None:
    bundle = repeated_tool_result_failure_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "tooling_blocked" in labels
    assert "agent_looping" not in labels


def test_agent_looping_detects_repeated_command_stderr_hashes() -> None:
    bundle = shared_stderr_distinct_command_failure_bundle()
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
    session_features = {feature.feature_name: feature for feature in features.session_features}
    repeated_command_failure = session_features["repeated_command_failure_count"]
    assert repeated_command_failure.feature_value == "2"
    assert repeated_command_failure.evidence["source_event_ids"] == [
        "event-1",
        "event-2",
        "event-3",
    ]
    groups = repeated_command_failure.evidence["groups"]
    assert len(groups) == 1
    assert groups[0]["group_type"] == "command_stderr_hash"


def test_agent_looping_ignores_small_command_group_mixed_with_tool_output_group() -> None:
    bundle = mixed_small_command_and_tool_failure_groups_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "tooling_blocked" in labels
    assert "agent_looping" not in labels
    session_features = {feature.feature_name: feature for feature in features.session_features}
    assert session_features["repeated_failure_count"].feature_value == "2"
    assert session_features["repeated_command_failure_count"].feature_value == "1"


def test_same_file_edit_evidence_deduplicates_source_event_ids() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    bundle = ParsedSessionBundle(
        session=session,
        file_activities=[
            FileActivity(
                file_activity_id="file-1",
                session_id=session.session_id,
                source_event_id="event-1",
                path="README.md",
                operation="edit",
            ),
            FileActivity(
                file_activity_id="file-2",
                session_id=session.session_id,
                source_event_id="event-1",
                path="README.md",
                operation="edit",
            ),
        ],
    )

    features = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in features.session_features}
    assert session_features["same_file_edited_repeatedly_count"].evidence == {
        "paths": {"README.md": 2},
        "source_event_ids": ["event-1"],
        "source_event_ids_by_path": {"README.md": ["event-1"]},
    }
