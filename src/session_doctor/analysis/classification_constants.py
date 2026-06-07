from __future__ import annotations

USER_STUCK_STUCKNESS_THRESHOLD = 0.45
TOOLING_BLOCKED_FAILED_COMMAND_RATIO_THRESHOLD = 0.50
TOOLING_BLOCKED_FAILED_TOOL_RESULT_RATIO_THRESHOLD = 0.50
TOOLING_BLOCKED_REPEATED_FAILURE_THRESHOLD = 2
AGENT_LOOPING_REPEATED_COMMAND_FAILURE_THRESHOLD = 2
RESOLVED_AFTER_CORRECTIONS_SCORE = 0.70
HEALTHY_SCORE_THRESHOLD = 0.25
AGENT_MISUNDERSTOOD_PROMPT_RISK_THRESHOLD = 0.35
PROMPT_AMBIGUOUS_THRESHOLD = 0.55
TASK_TOO_LARGE_COMPLEXITY_THRESHOLD = 0.65
TASK_TOO_LARGE_FRICTION_THRESHOLD = 0.35
REPO_COMPLEXITY_HIGH_THRESHOLD = 0.75

NEGATIVE_LABELS = frozenset(
    {
        "user_stuck",
        "tooling_blocked",
        "agent_looping",
        "agent_misunderstood",
        "prompt_ambiguous",
        "task_too_large",
        "repo_complexity_high",
        "abandoned_or_stopped",
    }
)

MISUNDERSTANDING_CORRECTION_FAMILIES = frozenset(
    {
        "not_what_i_asked",
        "not_what_i_meant",
        "misunderstood",
        "unexpected_action",
    }
)
