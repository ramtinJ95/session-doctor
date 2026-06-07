from __future__ import annotations

from session_doctor.analysis.similarity import (
    REPEAT_REQUEST_SIMILARITY_THRESHOLD,
    request_similarity,
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
