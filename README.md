# session-doctor

`session-doctor` is a local-first CLI for inspecting AI agent sessions.

The project is being built around a normalized session model so Codex, Claude
Code, Pi, and future agent logs can be inspected through the same shape. The
longer-term goal is to classify signs of repeated requests, user frustration,
stuckness, prompt ambiguity, agent loops, and project complexity.

Phase 1 creates the foundation only:

- Python package and CLI entry point
- Pydantic schema foundations
- DuckDB storage scaffold
- adapter discovery interfaces for Codex, Claude Code, and Pi
- test, lint, and type-check tooling

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
