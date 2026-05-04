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

Design references:

- [Design Plan](docs/session-doctor-design.md)
- [Phase 1 Plan](docs/phase-1-plan.md)

