# Pre-Phase-8 Codex/Pi Copied-Local Validation

Date: 2026-07-10

Base revision: `main` at `1b8def1`, plus the pre-Phase-8 closeout fixes recorded
with this note.

Validation used temporary isolated copies of one recent completed Codex session
and one recent completed Pi session. No native message text, commands, source
paths, tool output, diffs, or file content are recorded here.

## Source And Parser Evidence

- adapter parser version: `0.1.0` for Codex and Pi
- native Codex version: `0.144.0`
- native Pi version: not present in the selected session
- discovered candidates: 70 Codex and 120 Pi
- copied sources: 1 Codex and 1 Pi
- parsed sessions: 2
- skipped sources: 0
- unsupported structural warnings after closeout fixes: 0
- deliberate warnings: `codex_turn_aborted=1`

The first smoke exposed current Codex metadata drift: `world_state`, review-mode,
thread-settings, and context-compaction records were being reported as
unsupported. Those records are now counted as expected metadata-only shapes;
`turn_aborted` remains a deliberate warning because it is relevant ending
evidence. The smoke then completed with no unsupported warnings.

## Normalized Row Counts

| Table | Rows |
| --- | ---: |
| session sources | 2 |
| sessions | 2 |
| raw events | 2,749 |
| messages | 601 |
| tool calls | 583 |
| tool results | 583 |
| command runs | 169 |
| file activities | 288 |
| model usage | 623 |
| parse warnings | 1 |
| analysis runs | 2 |
| message features | 75 |
| session features | 50 |
| session classifications | 5 |

Both sessions completed ingestion, loading, deterministic analysis, and the
aggregate summary path. The aggregate reported two sessions and two analyzed
sessions.

## Classification Evidence And Limits

The Codex session produced `agent_looping`, `prompt_ambiguous`, and
`user_stuck`. The Pi session produced `abandoned_or_stopped` and `user_stuck`.
The smoke verified that these labels were generated and aggregated without
adapter-specific analysis branches. The private conversations were not
manually adjudicated, so this validation makes no semantic false-positive or
false-negative claim.

## Privacy And Cleanup

Only parser/native versions, structural type names, aggregate counts, warning
codes, and classification labels were inspected. Source paths and substantive
native values were neither printed nor retained in this note. Temporary copied
sources and the validation DuckDB database were deleted automatically, and the
temporary validation script was removed after the run.

The repository quality gate passed after the closeout fixes: Ruff formatting
and lint, `ty check`, and 187 tests.
