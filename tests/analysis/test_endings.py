from __future__ import annotations

from datetime import UTC, datetime, timedelta

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import analyze_features, classify_session
from session_doctor.analysis import ending as ending_helpers
from session_doctor.analysis.timeline import has_later_final_answer
from session_doctor.schemas import AgentName, NormalizedRole, RawEvent, Session

from .fixtures import (
    abandoned_or_stopped_bundle,
    bursty_timestamp_window_bundle,
    malformed_final_record_parse_warning_bundle,
    message,
    resolved_after_correction_bundle,
    resolved_failed_command_bundle,
    timestamp_window_parse_warning_bundle,
)


def test_classify_session_emits_abandoned_or_stopped_for_late_stop_marker() -> None:
    bundle = abandoned_or_stopped_bundle()
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    labels = {classification.label for classification in classifications}
    assert "abandoned_or_stopped" in labels
    session_features = {feature.feature_name: feature for feature in features.session_features}
    assert session_features["stop_or_pause_count"].feature_value == "1"


def test_abandoned_or_stopped_uses_timestamp_ending_window() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    start = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
            timestamp=start + timedelta(minutes=39 if index == 10 else index),
        )
        for index in range(1, 41)
    ]
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=[
            message(
                "message-1",
                NormalizedRole.USER,
                "Never mind, we can stop here.",
                "event-10",
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

    abandoned = next(
        classification
        for classification in classifications
        if classification.label == "abandoned_or_stopped"
    )
    assert abandoned.evidence_event_ids == ["event-10"]


def test_abandoned_or_stopped_uses_only_unresolved_late_stop_evidence() -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    raw_events = [
        RawEvent(
            event_id=f"event-{index}",
            source_id="source-1",
            agent_name=AgentName.CODEX,
            record_index=index,
        )
        for index in range(1, 9)
    ]
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=[
            message("message-1", NormalizedRole.USER, "Never mind for now.", "event-2"),
            message(
                "message-2",
                NormalizedRole.ASSISTANT,
                "Completed that part.",
                "event-4",
                metadata={"phase": "final_answer"},
            ),
            message("message-3", NormalizedRole.USER, "Not now, we can stop.", "event-8"),
        ],
    )
    features = analyze_features(bundle, analysis_run_id="analysis-1")

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=features.message_features,
        session_features=features.session_features,
    )

    abandoned = next(
        classification
        for classification in classifications
        if classification.label == "abandoned_or_stopped"
    )
    assert abandoned.evidence_event_ids == ["event-8"]
    assert "1 late stop or pause marker" in abandoned.evidence_summary


def test_stop_after_scope_instruction_does_not_emit_abandoned_or_stopped() -> None:
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
                "Stop after updating docs.",
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

    labels = {classification.label for classification in classifications}
    assert "abandoned_or_stopped" not in labels
    session_features = {feature.feature_name: feature for feature in features.session_features}
    assert session_features["stop_or_pause_count"].feature_value == "0"


def test_unresolved_ending_ignores_markers_resolved_by_final_answer() -> None:
    bundle = resolved_after_correction_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    assert session_features["correction_count"].feature_value == "1"
    assert session_features["unresolved_ending_signal"].feature_value == "false"


def test_unresolved_ending_ignores_failed_commands_resolved_by_final_answer() -> None:
    bundle = resolved_failed_command_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    assert session_features["failed_command_count"].feature_value == "1"
    assert session_features["unresolved_ending_signal"].feature_value == "false"

    classifications = classify_session(
        bundle,
        analysis_run_id="analysis-1",
        message_features=result.message_features,
        session_features=result.session_features,
    )

    assert "user_stuck" not in {classification.label for classification in classifications}


def test_unresolved_ending_ignores_short_session_missing_final_answer_only() -> None:
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
            ),
            RawEvent(
                event_id="event-2",
                source_id="source-1",
                agent_name=AgentName.CODEX,
                record_index=2,
            ),
        ],
        messages=[
            message(
                "message-1",
                NormalizedRole.USER,
                "Can you inspect the current repository state?",
                "event-1",
            ),
            message(
                "message-2",
                NormalizedRole.ASSISTANT,
                "I will inspect the files.",
                "event-2",
            ),
        ],
    )

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    assert session_features["unresolved_ending_signal"].feature_value == "false"
    assert session_features["unresolved_ending_signal"].evidence == {}


def test_ending_signal_unions_timestamp_window_with_event_count_window() -> None:
    bundle = bursty_timestamp_window_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    unresolved_evidence = session_features["unresolved_ending_signal"].evidence
    assert session_features["unresolved_ending_signal"].feature_value == "true"
    assert "correction_marker" in unresolved_evidence["late_message_features"]


def test_ending_signal_includes_parse_warnings_from_timestamp_window() -> None:
    bundle = timestamp_window_parse_warning_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    unresolved_evidence = session_features["unresolved_ending_signal"].evidence
    assert session_features["unresolved_ending_signal"].feature_value == "true"
    assert unresolved_evidence["late_parse_warning_ids"] == ["warning-1"]


def test_ending_signal_includes_malformed_final_record_parse_warning() -> None:
    bundle = malformed_final_record_parse_warning_bundle()

    result = analyze_features(bundle, analysis_run_id="analysis-1")

    session_features = {feature.feature_name: feature for feature in result.session_features}
    unresolved_evidence = session_features["unresolved_ending_signal"].evidence
    assert session_features["unresolved_ending_signal"].feature_value == "true"
    assert unresolved_evidence["late_parse_warning_ids"] == ["warning-1"]


def test_timeline_detects_later_final_answer() -> None:
    assert has_later_final_answer(2, [1, 3]) is True


def test_ending_helpers_use_ending_window_constants(monkeypatch) -> None:
    session = Session(
        session_id="session-1",
        source_id="source-1",
        agent_name=AgentName.CODEX,
    )
    start = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    bundle = ParsedSessionBundle(
        session=session,
        raw_events=[
            RawEvent(
                event_id=f"event-{index}",
                source_id="source-1",
                agent_name=AgentName.CODEX,
                record_index=index,
                timestamp=start + timedelta(minutes=index),
            )
            for index in range(1, 11)
        ],
    )

    monkeypatch.setattr(ending_helpers, "ENDING_WINDOW_MIN_EVENTS", 2)
    monkeypatch.setattr(ending_helpers, "ENDING_WINDOW_MAX_EVENTS", 2)
    monkeypatch.setattr(ending_helpers, "ENDING_WINDOW_FRACTION", 1.0)
    monkeypatch.setattr(ending_helpers, "ENDING_WINDOW_MINUTES", 1)

    assert ending_helpers.ending_record_index_start(bundle) == 9
    assert ending_helpers.timestamp_window_source_event_ids(bundle) == {"event-9", "event-10"}
    assert ending_helpers.ending_source_event_ids(bundle) == {"event-9", "event-10"}
