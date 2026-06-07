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

Initialize and inspect a DuckDB store:

```bash
uv run session-doctor db init
uv run session-doctor db info
```

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
