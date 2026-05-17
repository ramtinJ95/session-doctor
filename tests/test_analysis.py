from __future__ import annotations

from datetime import UTC, datetime, timedelta

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import (
    REPEAT_REQUEST_SIMILARITY_THRESHOLD,
    analyze_features,
    classify_session,
)
from session_doctor.analysis.features import request_similarity
from session_doctor.schemas import (
    AgentName,
    CommandRun,
    FileActivity,
    Message,
    NormalizedRole,
    RawEvent,
    Session,
    ToolResult,
)


def test_request_similarity_uses_fixture_calibrated_score_margins() -> None:
    positive_pairs = [
        (
            "Can you update the phase 3 plan with these decisions?",
            "Please update the phase-3 document to reflect what we decided.",
        ),
        (
            "Can we parse token_count as ModelUsage instead of a warning?",
            "I think token_count should become ModelUsage, not a warning.",
        ),
        (
            "Please fix the failing pytest in tests/test_cli.py",
            "Please fix the pytest failure in tests/test_cli.py.",
        ),
        (
            "Update docs/phase-3-plan.md with the artifact decision.",
            "Please update docs/phase-3-plan.md for the artifact decision.",
        ),
        (
            "Run ruff check and ty check before committing.",
            "Please run ty check and ruff check before the commit.",
        ),
        (
            "Keep the phase 3 implementation in small commits.",
            "Create small commits for the phase 3 implementation.",
        ),
        (
            "Ingest the copied Codex session fixture into DuckDB.",
            "Please ingest the copied Codex fixture into DuckDB.",
        ),
        (
            "Add tests for unresolved ending signal.",
            "Please add unresolved-ending signal tests.",
        ),
    ]
    negative_pairs = [
        (
            "Can you update the phase 3 plan with these decisions?",
            "Can you run the full test suite?",
        ),
        (
            "The warnings are too noisy, can we parse token_count properly?",
            "Please create a PR and merge it.",
        ),
        (
            "Please fix the failing pytest in tests/test_cli.py",
            "Explain what a DuckDB migration means.",
        ),
        (
            "Update docs/phase-3-plan.md with the artifact decision.",
            "List ingested sessions from the database.",
        ),
        (
            "Run ruff check and ty check before committing.",
            "Parse a Codex JSONL fixture.",
        ),
        (
            "Keep the phase 3 implementation in small commits.",
            "Show the session source path.",
        ),
        (
            "Ingest the copied Codex session fixture into DuckDB.",
            "Detect repeated user requests.",
        ),
        (
            "Add tests for unresolved ending signal.",
            "Create the GitHub pull request.",
        ),
    ]
    near_miss_pairs = [
        (
            "Update the phase 3 plan with the migration decision.",
            "Explain what an additive migration means.",
        ),
        (
            "Run ruff check and ty check before committing.",
            "Fix the ruff lint failure in the parser.",
        ),
        (
            "Ingest the copied Codex session fixture into DuckDB.",
            "List the ingested Codex sessions from DuckDB.",
        ),
        (
            "Add tests for unresolved ending signal.",
            "Explain what unresolved ending signal means.",
        ),
    ]

    positive_scores = [request_similarity(first, second) for first, second in positive_pairs]
    negative_scores = [request_similarity(first, second) for first, second in negative_pairs]
    near_miss_scores = [request_similarity(first, second) for first, second in near_miss_pairs]

    assert min(positive_scores) >= REPEAT_REQUEST_SIMILARITY_THRESHOLD
    assert max(negative_scores) < REPEAT_REQUEST_SIMILARITY_THRESHOLD
    assert max(near_miss_scores) < REPEAT_REQUEST_SIMILARITY_THRESHOLD
    assert min(positive_scores) - max(near_miss_scores) > 0.02


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
    assert session_features["repeated_command_failure_count"].feature_value == "2"
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
    assert {"user_stuck", "tooling_blocked", "agent_looping"}.issubset(labels)
    assert "resolved_after_corrections" not in labels
    tooling_blocked = next(
        classification
        for classification in classifications
        if classification.label == "tooling_blocked"
    )
    assert tooling_blocked.evidence_event_ids


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


def analysis_fixture_bundle() -> ParsedSessionBundle:
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
        for index in range(1, 11)
    ]
    messages = [
        message(
            "message-1",
            NormalizedRole.USER,
            "Please fix the failing pytest in tests/test_cli.py.",
            "event-1",
        ),
        message("message-2", NormalizedRole.ASSISTANT, "I will run the tests.", "event-2"),
        message(
            "message-3",
            NormalizedRole.USER,
            "Please fix the pytest failure in tests/test_cli.py.",
            "event-3",
        ),
        message(
            "message-4",
            NormalizedRole.USER,
            "No, that is not what I meant. It is still broken.",
            "event-8",
        ),
        message(
            "message-5",
            NormalizedRole.USER,
            "Before you change more code, keep it to small commits.",
            "event-9",
        ),
    ]
    command_runs = [
        CommandRun(
            command_run_id="command-1",
            session_id=session.session_id,
            source_event_id="event-5",
            command="pytest -q",
            exit_code=1,
            stdout_hash="hash-failure",
        ),
        CommandRun(
            command_run_id="command-2",
            session_id=session.session_id,
            source_event_id="event-10",
            command="pytest -q",
            exit_code=1,
            stdout_hash="hash-failure",
        ),
    ]
    tool_results = [
        ToolResult(
            tool_result_id="tool-result-1",
            session_id=session.session_id,
            source_event_id="event-6",
            is_error=True,
            output_hash="tool-error-hash",
        )
    ]
    file_activities = [
        FileActivity(
            file_activity_id="file-1",
            session_id=session.session_id,
            source_event_id="event-4",
            path="/tmp/example.py",
            operation="update",
        ),
        FileActivity(
            file_activity_id="file-2",
            session_id=session.session_id,
            source_event_id="event-7",
            path="/tmp/example.py",
            operation="update",
        ),
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
        tool_results=tool_results,
        file_activities=file_activities,
    )


def resolved_after_correction_bundle() -> ParsedSessionBundle:
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
        for index in range(1, 5)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please fix the failing test.", "event-1"),
        message(
            "message-2",
            NormalizedRole.USER,
            "No, that is not what I meant.",
            "event-2",
        ),
        message(
            "message-3",
            NormalizedRole.ASSISTANT,
            "Fixed and verified.",
            "event-4",
            metadata={"phase": "final_answer"},
        ),
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def resolved_failed_command_bundle() -> ParsedSessionBundle:
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
        for index in range(1, 5)
    ]
    messages = [
        message("message-1", NormalizedRole.USER, "Please run the test.", "event-1"),
        message(
            "message-2",
            NormalizedRole.ASSISTANT,
            "The failure is fixed now.",
            "event-4",
            metadata={"phase": "final_answer"},
        ),
    ]
    command_runs = [
        CommandRun(
            command_run_id="command-1",
            session_id=session.session_id,
            source_event_id="event-3",
            command="pytest -q",
            exit_code=1,
        )
    ]
    return ParsedSessionBundle(
        session=session,
        raw_events=raw_events,
        messages=messages,
        command_runs=command_runs,
    )


def bursty_timestamp_window_bundle() -> ParsedSessionBundle:
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
            timestamp=start + timedelta(seconds=index),
        )
        for index in range(1, 31)
    ]
    messages = [
        message(
            "message-1",
            NormalizedRole.USER,
            "No, that is not what I meant.",
            "event-5",
        )
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, messages=messages)


def repeated_command_failure_bundle() -> ParsedSessionBundle:
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
        for index in range(1, 4)
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            command="pytest -q",
            exit_code=1,
        )
        for index in range(1, 4)
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, command_runs=command_runs)


def repeated_tool_result_failure_bundle() -> ParsedSessionBundle:
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
        for index in range(1, 4)
    ]
    tool_results = [
        ToolResult(
            tool_result_id=f"tool-result-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            is_error=True,
            output_hash="same-tool-error",
        )
        for index in range(1, 4)
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, tool_results=tool_results)


def shared_stderr_distinct_command_failure_bundle() -> ParsedSessionBundle:
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
        for index in range(1, 4)
    ]
    command_runs = [
        CommandRun(
            command_run_id=f"command-{index}",
            session_id=session.session_id,
            source_event_id=f"event-{index}",
            command=f"pytest tests/test_{index}.py",
            exit_code=1,
            stderr_hash="shared-stderr",
        )
        for index in range(1, 4)
    ]
    return ParsedSessionBundle(session=session, raw_events=raw_events, command_runs=command_runs)


def message(
    message_id: str,
    role: NormalizedRole,
    text: str,
    source_event_id: str,
    metadata: dict[str, object] | None = None,
) -> Message:
    return Message(
        message_id=message_id,
        session_id="session-1",
        role=role,
        source_event_id=source_event_id,
        text=text,
        text_length=len(text),
        metadata=metadata or {},
    )
