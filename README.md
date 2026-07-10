# session-doctor

`session-doctor` is a local-first CLI for inspecting AI agent sessions.

The project is being built around a normalized session model so Codex, Claude
Code, Pi, and future agent logs can be inspected through the same shape. The
longer-term goal is to classify signs of repeated requests, user frustration,
stuckness, prompt ambiguity, agent loops, and project complexity.

Phase 1 created the foundation:

- Python package and CLI entry point
- Pydantic schema foundations
- DuckDB storage scaffold
- adapter discovery interfaces for Codex, Claude Code, and Pi
- test, lint, and type-check tooling

Phase 2 adds the first real vertical slice for Codex sessions:

- Codex JSONL parsing
- normalized DuckDB persistence
- `session-doctor ingest --agent codex`
- `session-doctor sessions list`

Phase 3 adds the first deterministic Codex analysis slice:

- derived feature and classification rows
- `session-doctor analyze <session-id>`
- terminal summaries
- default JSON analysis artifacts

Phase 4 adds Pi as the second native adapter:

- Pi JSONL parsing
- `session-doctor ingest --agent pi`
- existing `sessions list` and `analyze` behavior over Pi-derived records

Phase 5 hardens deterministic feature evidence:

- preserved analysis ordering and normalized timestamps
- richer repeated-failure, repeated-edit, and unresolved-ending evidence
- narrower command-loop classification behavior

Phase 6 adds classification scoring:

- reusable risk score features for friction, stuckness, prompt clarity, agent
  fit, and project complexity
- metadata-rich deterministic classifications
- conservative labels such as `healthy`, `agent_misunderstood`,
  `prompt_ambiguous`, `task_too_large`, `repo_complexity_high`, and
  `abandoned_or_stopped`

Phase 7 adds aggregate summaries:

- `session-doctor summary` over all ingested sessions
- optional `--agent` and `--project` filters
- terminal and JSON views over analysis coverage, labels, risky sessions,
  failed commands, repeated files, and next-step recommendations

Pre-Phase-8 PR 1 hardened that foundation:

- conservative canonical command and file identities shared by Codex and Pi
- explicit non-zero ingestion failures without hiding persistence errors
- all five risk scores in aggregate output with stable JSON precision
- deduplicated aggregate evidence IDs

Pre-Phase-8 PR 2 added the Claude Code root-session vertical slice:

- tolerant Claude root JSONL parsing into the existing normalized models
- `session-doctor ingest --agent claude` for root transcripts
- existing `sessions list`, `analyze`, and `summary` behavior over Claude roots
- hashed tool/command output and edit/write bodies without persisted thinking text

Pre-Phase-8 PR 3 completes the Claude adapter:

- root and subagent transcripts become separate linked sessions
- subagent metadata enriches sessions without becoming a session itself
- explicitly referenced tool-result sidecars contribute only hashes, lengths,
  and truncation facts
- memory, orphan sidecars, and auxiliary files remain visible but deliberately
  excluded from session ingestion
- copied-local validation exercises discovery, ingestion, analysis, summary,
  parent linkage, and privacy invariants

Phase 8 adds deterministic project-level trends:

- filtered `analyze --all` recovery for stale and never-analyzed sessions
- aligned weekly/monthly `trends` over current analyzer-version rows
- explicit coverage, score samples, classification/risk denominators, and empty
  periods
- separate top-level and sidechain cohorts with guarded project-scoped
  directions
- non-causal agent observations and exact observed project-path hints
- root-family recurring failed commands, opaque failed-tool fingerprints, and
  problematic files without exposing native output or content
- `projects list` discovery without guessed repository roots

Phase 9 adds exact-session diagnostics:

- privacy-safe terminal, Markdown, and stable JSON `report` output
- explicit current, stale, and missing analysis states without automatic analysis
- evidence-only message disclosure through `--show-text`
- trailing project recurrence context with fixed topology and timing boundaries
- complete deterministic JSON evidence graphs with conservative provenance
- read-only on-demand projection without report artifacts, graph persistence, or NetworkX

Phase 10 completes the roadmap with one optional portable agent skill for
Codex, Claude Code, and Pi plus a lightweight `v0.1.0` source-tag dogfood
release. The skill remains a thin CLI orchestrator: it never reads transcripts
or DuckDB directly, confirms before writes and evidence-message disclosure, and
keeps interpretations evidence-citing and non-causal. MCP, CI, PyPI, and stable
0.x compatibility remain deferred while the CLI is dogfooded.

## Usage

Install dependencies:

```bash
uv sync
```

Run the CLI:

```bash
uv run session-doctor --help
uv run session-doctor version
uv run session-doctor doctor
uv run session-doctor adapters list
uv run session-doctor adapters list --scan
```

## Optional Agent Skill

Phase 10 ships one portable `session-doctor` Agent Skill for Codex, Claude
Code, and Pi. It covers the public CLI without reading native transcripts or
DuckDB directly. It confirms before database/artifact writes, shell-completion
installation, or evidence-message disclosure.

Locate and inspect the exact skill bundled with the installed CLI:

```bash
skill_source="$(session-doctor integrations path)"
printf '%s\n' "$skill_source"
cat "$skill_source/SKILL.md"
```

When running from this source checkout, replace `session-doctor` above with
`uv run session-doctor`.

Install manually only after checking that the destination does not already
exist. Codex and Pi can share one global copy:

```bash
test ! -e "$HOME/.agents/skills/session-doctor"
mkdir -p "$HOME/.agents/skills"
cp -R "$skill_source" "$HOME/.agents/skills/session-doctor"
```

Claude Code uses its global skill root:

```bash
test ! -e "$HOME/.claude/skills/session-doctor"
mkdir -p "$HOME/.claude/skills"
cp -R "$skill_source" "$HOME/.claude/skills/session-doctor"
```

Pi may alternatively use `~/.pi/agent/skills/session-doctor`. Invoke the skill
as `$session-doctor` in Codex, `/session-doctor` in Claude Code, or
`/skill:session-doctor` in Pi. The skill requires exactly the CLI version named
in its frontmatter. Replace or remove an existing destination only through an
explicit manual decision; `session-doctor` never modifies agent configuration.

Initialize and inspect a DuckDB store:

```bash
uv run session-doctor db init
uv run session-doctor db info
```

Before version 1.0, incompatible database schemas are not migrated. Use
`db info` to inspect the stored schema version, then delete and rebuild an
incompatible local database.

Use a temporary or project-local database path during development:

```bash
uv run session-doctor db init --db /tmp/session-doctor-test.duckdb
SESSION_DOCTOR_DB=/tmp/session-doctor-test.duckdb uv run session-doctor db info
```

Ingest a Codex session file or directory:

```bash
uv run session-doctor ingest --agent codex \
  --source tests/fixtures/codex/basic-session.jsonl \
  --db /tmp/session-doctor-test.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-test.duckdb
```

If `--source` is omitted, Codex ingestion scans the default Codex session root:

```bash
uv run session-doctor ingest --agent codex --db /tmp/session-doctor-test.duckdb
```

Ingest a Pi session file or directory:

```bash
uv run session-doctor ingest --agent pi \
  --source tests/fixtures/pi/basic-session.jsonl \
  --db /tmp/session-doctor-test.duckdb
uv run session-doctor sessions list --db /tmp/session-doctor-test.duckdb
```

If `--source` is omitted, Pi ingestion scans the default Pi session root:

```bash
uv run session-doctor ingest --agent pi --db /tmp/session-doctor-test.duckdb
```

Ingest one Claude Code root or subagent transcript:

```bash
uv run session-doctor ingest --agent claude \
  --source tests/fixtures/claude/basic-session.jsonl \
  --db /tmp/session-doctor-test.duckdb
```

If `--source` is omitted, Claude ingestion scans `~/.claude/projects` but
selects root and subagent JSONL transcripts. Matched subagent metadata and
explicitly referenced persisted tool results enrich those sessions without
creating standalone sessions. Memory files, auxiliary files, and unreferenced
sidecars remain excluded; ingest output reports parsed and ignored source-kind
counts.

Analyze an ingested session:

```bash
uv run session-doctor analyze <session-id> --db /tmp/session-doctor-test.duckdb
uv run session-doctor analyze <session-id> \
  --db /tmp/session-doctor-test.duckdb \
  --format json
```

By default, `analyze` writes a JSON artifact beside the DuckDB file:

```text
<database-parent>/artifacts/<session-id>-analysis.json
```

Use `--no-artifact` to skip artifact writing or `--artifact <path>` to choose a
specific output path.

Restore current analysis coverage deliberately across matching sessions:

```bash
uv run session-doctor analyze --all --db /tmp/session-doctor-test.duckdb
uv run session-doctor analyze --all \
  --db /tmp/session-doctor-test.duckdb \
  --project /tmp/session-doctor \
  --agent codex
```

Batch analysis skips already-current sessions by default, continues after
per-session failures, and writes no artifacts unless `--write-artifacts` is
specified. Use `--force` to reanalyze already-current matching sessions.

Summarize the local store after ingesting and optionally analyzing sessions:

```bash
uv run session-doctor summary --db /tmp/session-doctor-test.duckdb
uv run session-doctor summary --db /tmp/session-doctor-test.duckdb --format json
uv run session-doctor summary --db /tmp/session-doctor-test.duckdb --agent pi
uv run session-doctor summary --db /tmp/session-doctor-test.duckdb --agent claude
uv run session-doctor summary \
  --db /tmp/session-doctor-test.duckdb \
  --project /tmp/session-doctor
```

`summary --limit` controls the maximum rows shown in ranked/detail sections,
such as risky sessions, failed commands, and repeated files.

Inspect aligned project-level trends and exact observed path hints:

```bash
uv run session-doctor trends --db /tmp/session-doctor-test.duckdb
uv run session-doctor trends \
  --db /tmp/session-doctor-test.duckdb \
  --project /tmp/session-doctor
uv run session-doctor trends \
  --db /tmp/session-doctor-test.duckdb \
  --bucket month \
  --periods 12 \
  --format json
uv run session-doctor projects list \
  --db /tmp/session-doctor-test.duckdb \
  --format json
```

Trend commands are read-only and never trigger ingestion or analysis. Global
views expose raw series but reserve directional judgments for an explicit
`--project` scope. Missing timestamps, stale analysis, sparse periods, and
insufficient samples remain visible rather than being filled or guessed.

Inspect one session as a report or typed evidence graph:

```bash
uv run session-doctor report <session-id> --db /tmp/session-doctor-test.duckdb
uv run session-doctor report <session-id> \
  --db /tmp/session-doctor-test.duckdb \
  --format markdown > report.md
uv run session-doctor report <session-id> \
  --db /tmp/session-doctor-test.duckdb \
  --format json
uv run session-doctor report <session-id> \
  --db /tmp/session-doctor-test.duckdb \
  --show-text
uv run session-doctor graph <session-id> \
  --db /tmp/session-doctor-test.duckdb
```

Both commands are exact-session and read-only. They never ingest, analyze,
write artifacts, cache projections, or persist graph rows. Stale or missing
analysis returns explicit partial output. `--show-text` applies only to report
evidence messages selected by persisted exact message IDs; graph output is
always message-text-free.

Run the quality gate:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
```

Design references:

- [Design Plan](docs/session-doctor-design.md)
- [Phase 1 Plan](docs/phase-1-plan.md)
- [Phase 2 Plan](docs/phase-2-plan.md)
- [Phase 3 Plan](docs/phase-3-plan.md)
- [Phase 4 Plan](docs/phase-4-plan.md)
- [Phase 5 Plan](docs/phase-5-plan.md)
- [Phase 6 Plan](docs/phase-6-plan.md)
- [Phase 7 Plan](docs/phase-7-plan.md)
- [Phase 8 Plan](docs/phase-8-plan.md)
- [Phase 8 Validation](docs/phase-8-validation.md)
- [Phase 9 Plan](docs/phase-9-plan.md)
- [Phase 9 Validation](docs/phase-9-validation.md)
- [Phase 10 Plan](docs/phase-10-plan.md)
- [Pre-Phase-8 Plan](docs/pre-phase-8-plan.md)
