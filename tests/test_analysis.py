from __future__ import annotations

from session_doctor.adapters import ParsedSessionBundle
from session_doctor.analysis import REPEAT_REQUEST_SIMILARITY_THRESHOLD, analyze_features
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
    assert session_features["same_file_edited_repeatedly_count"].feature_value == "1"
    assert session_features["max_edits_to_single_file"].feature_value == "2"
    assert session_features["unresolved_ending_signal"].feature_value == "true"


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


def message(
    message_id: str,
    role: NormalizedRole,
    text: str,
    source_event_id: str,
) -> Message:
    return Message(
        message_id=message_id,
        session_id="session-1",
        role=role,
        source_event_id=source_event_id,
        text=text,
        text_length=len(text),
    )
