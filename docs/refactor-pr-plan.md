# Refactor PR Plan: Split Large Modules Safely

Status: PRs 1-7 complete; optional PR 8 complete.

This plan breaks the current god-file cleanup into focused, reviewable PRs. The
goal is to reduce module size and coupling without changing behavior, schemas,
CLI contracts, artifact shape, feature names, classification labels, scores, or
stored row semantics.

Use this document as the starting point for future refactor work so the repo
does not need another broad discovery pass before implementation.

## Baseline Hotspots

This section captures the pre-refactor baseline used to create the PR sequence.
Items may be marked complete as the sequence lands.

- `src/session_doctor/analysis/features.py` — 1,243 lines. Owns marker
  vocabularies, request similarity, message/session features, repeated failure
  evidence, file edit evidence, risk-score formulas, and unresolved-ending
  evidence. Status: timeline and ending helpers extracted by PR 2; feature
  extraction internals split into focused modules by PR 3.
- `src/session_doctor/store/duckdb.py` — 1,012 lines. Contains a 686-line
  `DuckDBStore` class plus row serializers and JSON/DB helpers. Status: store
  connection helpers, readers, writers, row mappers/loaders, JSON helpers, and
  store dataclasses split into focused modules by PR 5.
- `src/session_doctor/analysis/classification.py` — 791 lines. Owns threshold
  constants, rule orchestration, rule implementations, evidence summaries, and
  final-answer/stop-pause ending helpers. Status: timeline and ending helpers
  extracted by PR 2; constants, context, factories, evidence helpers, and rules
  split into focused modules by PR 4.
- `src/session_doctor/cli.py` — 645 lines. Mixes Typer declarations, workflow
  orchestration, validation, Rich rendering, artifact writing, and JSON payloads.
  Status: CLI options, renderers, ingest workflow, analysis workflow, and
  artifact helpers split into focused modules by PR 7.
- `src/session_doctor/adapters/codex.py` — 621 lines. Mixes source discovery,
  parse dispatch, metadata extraction, warning policy, fallback message de-dupe,
  and Codex schema factories. Status: Codex metadata, records, messages, tools,
  commands, and file helpers split into focused modules by PR 6.
- `src/session_doctor/adapters/pi.py` — 430 lines and
  `src/session_doctor/adapters/pi_tools.py` — 528 lines. Pi parsing is partially
  extracted, but tool-call, tool-result, command, file, output/error, and usage
  concerns remain bundled together. Status: Pi metadata, records, correlation,
  messages, tool calls/results, commands, files, usage, and result heuristics
  split into focused modules by PR 6.
- `tests/test_analysis.py` — 1,616 lines before PR 1. Mega-test covering
  similarity, scoring helpers, feature extraction, classification, unresolved
  endings, repeated failures, and fixture builders. Status: split into
  `tests/analysis/` by PR 1.

## Refactor Ground Rules

- Treat every PR as a mechanical extraction unless explicitly stated otherwise.
- Preserve public imports where practical by keeping facade modules in place.
- Preserve these entry points:
  - `session_doctor.analysis.analyze_features`
  - `session_doctor.analysis.classify_session`
  - `session_doctor.store.DuckDBStore`
  - `CodexAdapter.parse_source`
  - `PiAdapter.parse_source`
  - CLI command names/options/output shape
- Do not change normalized schema models, DuckDB table shape, feature names,
  classification labels, score formulas, thresholds, JSON artifact shape, or
  terminal summary semantics during these PRs.
- Keep old modules as compatibility facades when splitting would otherwise
  force broad import churn.
- Prefer small conventional commits within each PR.
- After each PR, run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

If an intermediate commit is intentionally mechanical and type checking is noisy
until the last commit, still ensure the final PR state passes all four commands.

## PR 1 — Split Analysis Tests and Fixtures

Status: complete.

Risk: low. Production behavior should not change.

Purpose: make later feature/classification refactors easier to review and safer
to run in focused test subsets.

Starting file:

- `tests/test_analysis.py`

Target shape:

```text
tests/analysis/
  __init__.py
  fixtures.py
  test_similarity.py
  test_score_helpers.py
  test_features.py
  test_classification.py
  test_endings.py
  test_repeated_failures.py
```

Suggested moves:

- Move fixture builders from `tests/test_analysis.py:974-1616` into
  `tests/analysis/fixtures.py`.
- Move request similarity tests around `tests/test_analysis.py:33-149` into
  `test_similarity.py` and `test_score_helpers.py`.
- Move general feature extraction tests around `tests/test_analysis.py:150-364`
  into `test_features.py`.
- Move classification label tests around `tests/test_analysis.py:365-609` into
  `test_classification.py`.
- Move unresolved-ending/timestamp-window tests around
  `tests/test_analysis.py:610-848` into `test_endings.py`.
- Move repeated command/tool/file evidence tests around
  `tests/test_analysis.py:851-938` into `test_repeated_failures.py`.
- Delete the original `tests/test_analysis.py` only after imports and test
  collection work.

Acceptance criteria:

- Test count remains stable unless duplicate/import-only tests are intentionally
  removed.
- No production files changed except possibly import paths exposed only for test
  readability.
- `uv run pytest tests/analysis -q` passes.
- Full validation passes.

## PR 2 — Extract Shared Analysis Timeline and Ending Helpers

Status: complete.

Risk: low-to-medium. This touches behavior-adjacent helper code used by both
features and classification, so move code first before improving it.

Purpose: remove duplicated final-answer and ending-window logic before splitting
feature and classification modules further.

Starting files:

- `src/session_doctor/analysis/features.py:895-1042`
- `src/session_doctor/analysis/classification.py:635-717`

Target shape:

```text
src/session_doctor/analysis/
  timeline.py
  ending.py
  features.py
  classification.py
```

Suggested extractions:

- `timeline.py`
  - event-id to record-index mapping
  - assistant final-answer index detection
  - `has_later_final_answer(...)`
  - `has_assistant_final_answer(...)`
- `ending.py`
  - `ENDING_WINDOW_MIN_EVENTS`
  - `ENDING_WINDOW_MAX_EVENTS`
  - `ENDING_WINDOW_FRACTION`
  - `ENDING_WINDOW_MINUTES`
  - `ending_source_event_ids(...)`
  - `ending_record_index_start(...)`
  - `timestamp_window_source_event_ids(...)`
  - unresolved-ending evidence helpers that are shared or can be shared later

Compatibility requirements:

- If tests or downstream imports refer to existing functions in `features.py`,
  re-export thin wrappers from `features.py` temporarily.
- `classification.py` should import shared helpers from `analysis.ending` or
  `analysis.timeline`, not from `features.py`.

Acceptance criteria:

- Ending-related tests pass independently:

```bash
uv run pytest tests/analysis/test_endings.py -q
```

- Full validation passes.

## PR 3 — Split Feature Extraction Into Focused Modules

Status: complete.

Risk: medium. This is a large mechanical split of central analysis code.

Purpose: reduce `features.py` from a god module into a facade/orchestrator over
focused feature modules.

Starting file:

- `src/session_doctor/analysis/features.py`

Target shape:

```text
src/session_doctor/analysis/
  features.py              # facade/orchestrator; exports analyze_features
  feature_models.py        # ExtractedFeatures, RequestSignature, SessionFeatureContext
  feature_factories.py     # message_feature/session_feature helpers
  markers.py               # marker dictionaries and marker feature extraction
  similarity.py            # request signatures and similarity scoring
  session_counts.py        # message/command/tool count features
  repeated_failures.py     # repeated failure grouping/evidence
  file_features.py         # file edit/repeated edit features
  scoring.py               # risk score formulas and helpers
  ending.py                # from PR 2
```

Suggested move order:

1. Move dataclasses to `feature_models.py`.
2. Move factory helpers near the bottom of `features.py` into
   `feature_factories.py`.
3. Move request similarity constants/functions into `similarity.py`.
4. Move marker dictionaries and `marker_features(...)` into `markers.py`.
5. Move repeated-failure grouping and evidence helpers into
   `repeated_failures.py`.
6. Move file activity feature helpers into `file_features.py`.
7. Move risk-score formulas from `features.py:548-760` into `scoring.py`.
8. Keep `features.py` responsible for `analyze_features(...)`,
   `session_count_features(...)`, and orchestration only.

Important invariants:

- Feature names must remain identical.
- Evidence key names and metadata key names must remain identical.
- Score rounding and `feature_value` formatting must remain identical.
- Ordering of emitted features should remain stable where tests assert it or
  artifacts depend on it.

Acceptance criteria:

- `features.py` is mostly orchestration and compatibility exports.
- Focused tests pass:

```bash
uv run pytest tests/analysis/test_similarity.py tests/analysis/test_features.py tests/analysis/test_score_helpers.py tests/analysis/test_repeated_failures.py -q
```

- Full validation passes.

## PR 4 — Split Classification Rules and Evidence Helpers

Status: complete.

Risk: medium. Classification output is user-facing and persisted.

Purpose: turn `classification.py` into a small rule runner with rules and
evidence helpers in dedicated modules.

Starting file:

- `src/session_doctor/analysis/classification.py`

Target shape:

```text
src/session_doctor/analysis/
  classification.py              # public classify_session facade/rule runner
  classification_context.py      # ClassificationContext and feature accessors
  classification_constants.py    # thresholds and label sets
  classification_factories.py    # classification(...) and metadata helpers
  classification_evidence.py     # summaries, phrases, evidence event IDs
  classification_rules/
    __init__.py
    user_stuck.py
    tooling_blocked.py
    agent_looping.py
    resolved.py
    prompt_quality.py
    task_size.py
    abandoned.py
    healthy.py
```

Suggested move order:

1. Extract constants and negative-label sets.
2. Extract `ClassificationContext` and feature accessors.
3. Extract classification factory/metadata helpers.
4. Extract evidence phrase helpers and evidence-event-id helpers.
5. Move one rule at a time into `classification_rules/` while preserving rule
   ordering in `classify_session(...)`.
6. Keep `classification.py` as the public entry point and rule registry.

Important invariants:

- Labels, scores, confidence values, metadata, evidence summaries, and evidence
  event IDs must remain identical.
- Rule ordering must remain identical.
- `healthy` remains mutually exclusive with negative labels.
- `resolved_after_corrections` may still coexist with earlier negative evidence.

Acceptance criteria:

- Focused classification tests pass:

```bash
uv run pytest tests/analysis/test_classification.py tests/analysis/test_endings.py -q
```

- Full validation passes.

## PR 5 — Split DuckDB Store Into Repositories and Mappers

Status: complete.

Risk: medium-to-high. Store behavior is central to ingest, list, analyze, and
artifact workflows.

Purpose: reduce `duckdb.py` to the public store facade while moving SQL loaders,
writers, and row mapping into focused modules.

Starting file:

- `src/session_doctor/store/duckdb.py`

Target shape:

```text
src/session_doctor/store/
  duckdb.py             # DuckDBStore public facade and StoreInfo/SessionSummary
  connection.py         # connect/apply_migrations helpers, transaction helper
  writers.py            # insert parsed bundle, replace analysis rows, deletes
  readers.py            # list summaries, load bundle, info/table count helpers
  row_mappers.py        # schema model -> row dict serializers
  row_loaders.py        # row tuples -> Pydantic schema models
  json_values.py        # metadata_json, duckdb_value, parse_metadata/list helpers
  migrations.py
```

Suggested move order:

1. Move `metadata_json`, `duckdb_value`, `parse_metadata`, and
   `parse_string_list` into `json_values.py`.
2. Move row serializers from `duckdb.py:746-979` into `row_mappers.py`.
3. Move row-to-model loader bodies from private `_load_*` methods into
   `row_loaders.py`, called by `DuckDBStore` initially.
4. Move insert/delete transaction logic into `writers.py` while preserving
   `DuckDBStore.insert_parsed_bundle(...)` and
   `DuckDBStore.replace_analysis_rows(...)` as forwarding methods.
5. Move summary and bundle read SQL into `readers.py` while preserving
   `DuckDBStore.list_session_summaries(...)`, `load_session_bundle(...)`,
   `table_count(...)`, and `info(...)`.
6. Keep `DuckDBStore` as the only public object imported by CLI/tests.

Important invariants:

- Table names, column names, row insertion order, delete/replace semantics, and
  DB timestamp normalization must remain identical.
- Existing local DuckDB files are pre-1.0 and can be rebuilt, but this PR should
  not intentionally change schema or migration behavior.
- Keep `TABLE_NAMES` exported from `session_doctor.store` as before.

Acceptance criteria:

- Store tests pass:

```bash
uv run pytest tests/test_store.py tests/test_cli.py -q
```

- Full validation passes.

## PR 6 — Split Codex and Pi Adapter Internals

Status: complete.

Risk: medium. Adapter parsing is fixture-heavy and source-format-sensitive.

Purpose: keep adapter public classes stable while moving format-specific parsing
concerns into focused modules.

Starting files:

- `src/session_doctor/adapters/codex.py`
- `src/session_doctor/adapters/pi.py`
- `src/session_doctor/adapters/pi_tools.py`

Target shape:

```text
src/session_doctor/adapters/
  codex.py                    # CodexAdapter facade
  codex_metadata.py
  codex_records.py            # record dispatch/parse loop helpers
  codex_messages.py
  codex_tools.py
  codex_commands.py
  codex_files.py
  pi.py                       # PiAdapter facade
  pi_metadata.py
  pi_records.py
  pi_correlation.py
  pi_tool_calls.py
  pi_tool_results.py
  pi_commands.py
  pi_files.py
  pi_usage.py
  pi_result_heuristics.py
```

Codex suggested move order:

1. Move `extract_session_metadata(...)` and `CodexSessionMetadata` to
   `codex_metadata.py`.
2. Move message constructors and fallback de-dupe helpers to `codex_messages.py`.
3. Move tool-call/tool-result constructors to `codex_tools.py`.
4. Move command helpers to `codex_commands.py`.
5. Move patch/file activity helpers to `codex_files.py`.
6. Optionally move the parse dispatch loop to `codex_records.py`; keep
   `CodexAdapter.parse_source(...)` as a thin wrapper.

Pi suggested move order:

1. Move `PiCommandCorrelation` to `pi_correlation.py`.
2. Move `extract_session_metadata(...)` and `PiSessionMetadata` to
   `pi_metadata.py`.
3. Move message/role/phase helpers to a focused Pi message module if needed.
4. Split `pi_tools.py` into tool calls, tool results, commands, files, usage,
   and result heuristics.
5. Optionally move the parse dispatch loop to `pi_records.py`; keep
   `PiAdapter.parse_source(...)` as a thin wrapper.

Important invariants:

- `CodexAdapter.discover/parse_source` and `PiAdapter.discover/parse_source`
  behavior must remain identical.
- IDs, hashes, warning IDs, warning messages, metadata payloads, command/file
  extraction, and counts must remain identical.

Acceptance criteria:

- Adapter tests pass:

```bash
uv run pytest tests/test_codex_adapter.py tests/test_pi_adapter.py tests/test_adapters_base.py -q
```

- CLI ingest/list/analyze smoke tests still pass through `tests/test_cli.py`.
- Full validation passes.

## PR 7 — Split CLI Workflows, Renderers, and Artifacts

Status: complete.

Risk: low-to-medium. CLI output is user-facing, but extraction can be mostly
mechanical after analysis/store/adapters are smaller.

Purpose: keep Typer command functions thin and move orchestration/rendering into
dedicated modules.

Starting file:

- `src/session_doctor/cli.py`

Target shape:

```text
src/session_doctor/
  cli.py                  # Typer app/subcommands only
  cli_options.py          # db path/source/format validation helpers
  cli_renderers.py        # Rich tables and terminal summaries
  analysis_workflow.py    # analyze-session orchestration
  ingest_workflow.py      # ingest orchestration and IngestSummary
  artifacts.py            # artifact path and payload writing
```

Suggested move order:

1. Move `IngestSummary`, ingest accumulation, and source loop into
   `ingest_workflow.py` while preserving CLI command behavior.
2. Move `analysis_payload`, artifact path selection, and artifact writing into
   `artifacts.py`.
3. Move analyze orchestration into `analysis_workflow.py`.
4. Move Rich table functions and `ANALYSIS_SUMMARY_FEATURES` into
   `cli_renderers.py`.
5. Move DB/source validation helpers into `cli_options.py`.
6. Leave `cli.py` with Typer app setup, command signatures, and calls into the
   workflow/renderer modules.

Important invariants:

- Command names, option names, option defaults, exit codes, JSON output, default
  artifact path, and terminal summary rows must remain identical.
- `session-doctor = "session_doctor.cli:app"` in `pyproject.toml` stays valid.

Acceptance criteria:

- CLI tests pass:

```bash
uv run pytest tests/test_cli.py -q
```

- Manual smoke checks still work against a temp DB:

```bash
rm -f /tmp/session-doctor-refactor.duckdb
uv run session-doctor db init --db /tmp/session-doctor-refactor.duckdb
uv run session-doctor ingest --agent codex --source tests/fixtures/codex/basic-session.jsonl --db /tmp/session-doctor-refactor.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-refactor.duckdb
uv run session-doctor analyze <session-id> --db /tmp/session-doctor-refactor.duckdb --format json
```

- Full validation passes.

## Optional PR 8 — Remove Temporary Compatibility Re-exports

Status: complete.

Risk: low-to-medium, depending on downstream import assumptions.

Do this only after all extraction PRs have landed and no internal code still
depends on old private locations.

Possible cleanup:

- Remove compatibility wrappers from `features.py` and `classification.py` for
  helpers that are no longer part of the intended public surface.
- Update tests to import helpers from their new focused modules.
- Confirm `src/session_doctor/analysis/__init__.py` still exposes only intended
  public API.

Acceptance criteria:

- No behavior changes.
- Full validation passes.

## Preferred Branch/Commit Strategy

For each PR:

1. Start from a clean main branch.
2. Create a focused branch, e.g. `refactor/split-analysis-tests`.
3. Make mechanical moves first.
4. Fix imports and run focused tests.
5. Run full validation.
6. Keep PR description concise:

```text
Refactors <area> into smaller modules without changing behavior.

Validation:
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Use conventional commit messages, for example:

```text
refactor(tests): split analysis tests by concern
refactor(analysis): extract ending timeline helpers
refactor(store): move duckdb row mappers
```

## Stop Conditions

Pause and reassess before continuing a PR if any of these happen:

- A test failure reveals a behavior change rather than an import/move issue.
- Feature/classification output ordering changes.
- JSON artifact shape changes.
- DuckDB row values or counts change for existing fixtures.
- The extraction requires schema, migration, CLI contract, or label/score
  changes.
- A PR grows beyond one coherent area.

If a behavior improvement is discovered during extraction, log it in
`scratch/BACKLOG.md` with file and line numbers, then keep the current PR purely
mechanical.
