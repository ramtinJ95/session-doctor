# session-doctor

Session Doctor captures local Codex, Claude Code, and Pi session evidence into
DuckDB and analyzes it deterministically without production LLM or network
calls.

The v2 analyzer treats native sessions as provenance containers and emits
event-anchored task episodes. Exact compressed source snapshots are durable;
normalization and semantic projections are rebuildable.

## Install and run

```bash
uv sync
uv run session-doctor doctor
uv run session-doctor adapters
uv run session-doctor db init

uv run session-doctor ingest --agent codex
uv run session-doctor ingest --agent claude
uv run session-doctor ingest --agent pi
uv run session-doctor sessions list
```

Analyze one normalized native session:

```bash
uv run session-doctor analyze <session-id>
uv run session-doctor analyze <session-id> --format json
```

Output contains deterministic episodes, event-anchored boundary decisions,
lifecycle state, provisionality, and observable segmentation observations. It
does not contain v1 scores, labels, recommendations, or LLM judgments.

## Snapshot and normalization history

```bash
uv run session-doctor snapshots list
uv run session-doctor snapshots show <snapshot-id>
uv run session-doctor snapshots replay <snapshot-id> --output restored.jsonl
uv run session-doctor snapshots prune <snapshot-id>

uv run session-doctor normalizations status <snapshot-id>
uv run session-doctor normalizations replay <snapshot-id>
```

Replay never overwrites an existing path without `--overwrite`. Referenced
snapshots cannot be removed without `--force`, and partial frozen evaluation
corpora cannot be pruned.

## Offline evaluation

```bash
uv run session-doctor evaluation export-boundaries <normalization-run-id> \
  --output packets
uv run session-doctor evaluation export-pilot --output pilot-packets
uv run session-doctor evaluation import-judge --input judge-annotation.json
```

Judge-visible packets are written separately from private routing envelopes.
Session Doctor performs no provider calls.

## Temporary command availability

The following commands are deliberately unavailable while their v2 projections
are rebuilt:

```text
summary
trends
projects list
report
graph
```

Each fails explicitly with:

```text
<command> is unavailable during the deterministic analysis v2 rebuild; see docs/deterministic-analysis-v2-plan.md.
```

There is no v1 fallback.

## Command reference

| Command | Purpose |
| --- | --- |
| `doctor` | Check local prerequisites and adapter roots |
| `adapters` | List built-in adapters |
| `db init` / `db info` | Initialize or inspect DuckDB |
| `ingest` | Capture exact bytes and normalize selected sessions |
| `sessions list` | List stored native sessions |
| `analyze SESSION_ID` | Emit deterministic episode/lifecycle/observation output |
| `snapshots list/show/replay/prune` | Inspect and explicitly manage exact history |
| `normalizations status/replay` | Inspect or explicitly add parser projections |
| `evaluation export-boundaries` | Register routing privately and export judge packets |
| `evaluation export-pilot` | Register and export the packaged development pilot |
| `evaluation import-judge` | Import one offline judge annotation |
| `integrations path` | Print the bundled Agent Skill directory |

Most data commands accept `--db PATH`. The default database path follows the
platform app-data directory and can be overridden with `SESSION_DOCTOR_DB`.

## Privacy

- Source bytes, prompts, tool arguments, routing identities, and DuckDB data are
  private local artifacts.
- Production analysis is deterministic and local.
- Unknown, ambiguous, active, incomplete, provisional, delegated, and
  mixed-model states remain explicit.
- Exact source bytes are parsed from the same immutable snapshot stored in
  DuckDB.

See `docs/session-doctor-design.md` for the current architecture and
`docs/deterministic-analysis-v2-plan.md` for the implementation roadmap.
