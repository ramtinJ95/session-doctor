# session-doctor

`session-doctor` is a local CLI for understanding how AI coding-agent sessions
went: where work flowed, where it became difficult, and which problems keep
coming back.

It reads native session logs from **Codex**, **Claude Code**, and **Pi**, turns
them into one common local history, and applies deterministic rules to surface
signals such as:

- repeated requests, corrections, frustration, and unclear scope;
- failed commands, failed tools, and repeated failure loops;
- repeated edits to the same files and unresolved endings;
- sessions that look healthy, stuck, blocked, looping, misunderstood, too
  large, or unusually complex;
- recurring command, tool, and file patterns across sessions;
- weekly or monthly changes within an observed project path.

The tool does not call an LLM, upload session data, or claim to know intent or
causality. Its findings are explainable signals for review, not judgments about
a user or agent.

> **Status:** `v0.1.0` is a source-tag dogfood release. The project is not on
> PyPI yet, and 0.x CLI, database, and artifact formats may change. Rebuilding
> local data after an upgrade may be required.

## How it works

```text
native Codex / Claude Code / Pi logs
  -> adapter-specific parsing
  -> common session timeline in local DuckDB
  -> deterministic features, scores, and classifications
  -> summaries, trends, reports, and evidence graphs
```

Ingestion preserves source provenance while normalizing messages, tool calls
and results, commands, file activity, model usage, and parse warnings. Analysis
then derives evidence-backed features and classifications. Reports and graphs
read those stored results; they never silently ingest or analyze sessions.
Standalone HTML reports and dashboards render the same typed results and write
only the explicit output file selected by the user.

### What it measures

Five scores summarize different kinds of evidence:

| Score | What it represents |
| --- | --- |
| Friction | Corrections, failures, repeated work, and unresolved progress |
| Stuckness | Repetition, failure loops, frustration, and unresolved endings |
| Prompt clarity risk | Ambiguity, scope changes, and corrective user messages |
| Agent-fit risk | Evidence that the session or task is not progressing well with the current approach |
| Project-complexity signal | Broad file activity, repeated edits, and session scale |

Scores can support multiple labels, including `healthy`, `user_stuck`,
`tooling_blocked`, `agent_looping`, `agent_misunderstood`, `prompt_ambiguous`,
`task_too_large`, `repo_complexity_high`, `resolved_after_corrections`, and
`abandoned_or_stopped`.

Aggregate views also show analysis coverage, risky sessions, common labels,
failed commands, problematic files, recurrence across independent session
families, and guarded weekly/monthly trends. Sparse or incompatible evidence is
reported as missing, stale, or `insufficient_data` rather than guessed.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ramtinJ95/session-doctor.git
cd session-doctor
uv sync

# Check the environment and see what local sources are available.
uv run session-doctor doctor
uv run session-doctor adapters list --scan

# Create the local store, ingest one or more agents, and analyze new sessions.
uv run session-doctor db init
uv run session-doctor ingest --agent codex
uv run session-doctor ingest --agent claude
uv run session-doctor ingest --agent pi
uv run session-doctor analyze --all

# Review the result.
uv run session-doctor summary
uv run session-doctor sessions list --agent codex
uv run session-doctor report <session-id> --agent codex
```

You only need to ingest the agents you use. Without `--source`, each adapter
scans its standard local root:

| Agent | Default source root |
| --- | --- |
| Codex | `~/.codex/sessions` |
| Claude Code | `~/.claude/projects` |
| Pi | `~/.pi/agent/sessions` |

The default database is
`~/.local/share/session-doctor/session-doctor.duckdb`. Override it with
`--db PATH` or the `SESSION_DOCTOR_DB` environment variable.

To ingest a copied file or a specific directory instead of the live default
root:

```bash
uv run session-doctor ingest --agent codex --source /path/to/session.jsonl
uv run session-doctor ingest --agent claude --source /path/to/copied-sessions
```

Re-ingesting a source replaces that source's normalized rows and invalidates
its old analysis. Exact compressed source snapshots remain available for local
time travel; native source files are never modified.

## Common workflows

### Analyze sessions

```bash
# One session; writes a JSON artifact by default.
uv run session-doctor analyze <session-id> --agent codex

# One session without an artifact.
uv run session-doctor analyze <session-id> --agent codex --no-artifact

# Restore missing or stale analysis without batch artifacts.
uv run session-doctor analyze --all --agent codex

# Reanalyze every matching session and write per-session artifacts.
uv run session-doctor analyze --all --project /path/to/project \
  --force --write-artifacts
```

Single-session artifacts default to:
`<database-parent>/artifacts/<session-id>-analysis.json`.

### Summaries and trends

```bash
uv run session-doctor summary --agent claude --limit 20
uv run session-doctor summary --project /path/to/project --format json

uv run session-doctor projects list
uv run session-doctor trends --project /path/to/project
uv run session-doctor trends --project /path/to/project \
  --bucket month --periods 12 --format json
uv run session-doctor trends --project /path/to/project \
  --format html --output trends.html
```

Project values are exact observed path hints, not inferred repository roots.
Trends are database-read-only. HTML mode atomically replaces the explicit
`--output` file; its parent directory must already exist. Directional
statements require an explicit project scope and enough compatible evidence.

### Inspect one session

```bash
uv run session-doctor report <session-id> --agent codex
uv run session-doctor report <session-id> --agent codex \
  --format markdown > report.md
uv run session-doctor report <session-id> --agent codex --format json
uv run session-doctor report <session-id> --agent codex \
  --format html --output report.html
uv run session-doctor graph <session-id> --agent codex > graph.json
```

`report` and `graph` are exact-session, database-read-only views. They do not
ingest, analyze, write database rows, or cache derived data. Report HTML mode
atomically replaces only the explicit output file; it creates no directory,
sibling asset, or browser window. Stale or missing analysis is shown explicitly.

### Inspect exact source history

```bash
uv run session-doctor snapshots list --status settled_unknown --format json
uv run session-doctor snapshots show <snapshot-id>
uv run session-doctor snapshots replay <snapshot-id> --output replay.jsonl
uv run session-doctor snapshots replay <snapshot-id> --bundle --output replay-bundle
uv run session-doctor normalizations status <snapshot-id>
uv run session-doctor normalizations replay <snapshot-id>
uv run session-doctor snapshots prune <snapshot-id>
uv run session-doctor snapshots prune <snapshot-id> --force
```

Replay writes exact sensitive source bytes only to the explicit output path.
It refuses an existing file unless `--overwrite` is supplied; bundle export
always requires a new directory and includes an ordered manifest.
Pruning blocks current normalized dependencies unless `--force`; forced pruning
reports those dependencies, removes the complete bundle capture, and
checkpoints DuckDB.

## CLI reference

Run `uv run session-doctor COMMAND --help` for the authoritative help for any
command.

| Command | Purpose | Useful parameters |
| --- | --- | --- |
| `version` | Print the installed version | — |
| `doctor` | Check Python, DuckDB, paths, and adapter roots | `--db PATH` |
| `adapters list` | Show built-in adapters and roots | `--scan` |
| `db init` | Create the DuckDB store | `--db PATH` |
| `db info` | Show database path and schema status | `--db PATH` |
| `ingest` | Parse and store native sessions | `--agent codex\|claude\|pi`, `--source PATH`, `--db PATH` |
| `sessions list` | List ingested sessions | `--agent NAME`, `--db PATH` |
| `snapshots list` | List exact history and lifecycle | `--agent NAME`, `--status STATE`, `--format terminal\|json` |
| `snapshots show SNAPSHOT_ID` | Show snapshot provenance | `--db PATH` |
| `snapshots replay SNAPSHOT_ID` | Write exact captured bytes or bundle | `--output PATH`, `--bundle`, `--overwrite`, `--db PATH` |
| `normalizations status SNAPSHOT_ID` | Show current, stale, or missing parser coverage | `--db PATH` |
| `normalizations replay SNAPSHOT_ID` | Explicitly add current parser output from stored bytes | `--db PATH` |
| `snapshots prune SNAPSHOT_ID` | Explicitly prune a bundle capture | `--force`, `--db PATH` |
| `analyze SESSION_ID` | Analyze one session | `--agent NAME`, `--format terminal\|json`, `--artifact PATH`, `--no-artifact` |
| `analyze --all` | Restore or rebuild analysis coverage | `--project PATH`, `--agent NAME`, `--force`, `--write-artifacts` |
| `summary` | Show aggregate diagnostics | `--project PATH`, `--agent NAME`, `--limit N`, `--format terminal\|json` |
| `trends` | Show aligned trends and recurrence | `--format terminal\|json\|html`, `--output PATH` for HTML, `--bucket week\|month`, `--periods 1..120`, plus summary filters |
| `projects list` | List observed project/CWD hints | `--agent NAME`, `--limit N`, `--format terminal\|json` |
| `report SESSION_ID` | Build an exact-session report | `--agent NAME`, `--format terminal\|markdown\|json\|html`, `--output PATH` for HTML, `--limit N`, `--show-text` |
| `graph SESSION_ID` | Build an exact-session evidence graph | `--agent NAME`; JSON only |
| `integrations path` | Locate the bundled Agent Skill | — |

Most query commands accept `--db PATH`. `summary`, `trends`, `projects list`,
`report`, and `graph` are database-read-only. `report` and `trends` HTML modes
are explicit filesystem writes; `--output` is required for HTML and rejected
for other formats. `db init`, `ingest`, and `analyze` write local state.

## Privacy and local data

Session Doctor is local-first, but its DuckDB file still contains sensitive
local data needed for analysis:

- user and assistant message text, command text, and paths are stored locally;
- DuckDB retains compressed exact native snapshots; treat the database and raw
  replay files as private transcript data;
- raw tool/command output, diffs, file bodies, and full argument payloads are
  generally replaced by hashes, lengths, and structural metadata; selected
  fields such as paths, URLs, and search queries may still be stored locally;
- reports omit message text by default;
- `report --show-text` reveals only the displayed persisted evidence messages;
- standalone HTML is one self-contained offline file with no remote resources,
  network requests, telemetry, browser storage, database path, or sibling assets;
- graphs never include message text;
- displayed command examples and home paths are redacted;
- evidence text exposed by `--show-text` is otherwise verbatim and may itself
  contain sensitive data.

Treat the database, analysis artifacts, and generated HTML as private. Choose
output paths deliberately: HTML always replaces the named file and may contain
redacted paths, commands, evidence IDs, and explicitly requested bounded
message text. The output path itself can reveal sensitive context, so avoid
shared locations. The tool has no telemetry and makes no external API or model
calls.

## Optional Agent Skill

The package includes one portable skill for Codex, Claude Code, and Pi. It is a
thin wrapper around the public CLI and does not read transcripts or DuckDB
directly.

```bash
uv run session-doctor integrations path
```

Inspect the returned `SKILL.md` before manually copying that directory into the
appropriate agent skill root. The CLI never installs or modifies agent
configuration automatically. The skill itself documents supported destinations
and confirmation rules.

## Current limitations

- Native agent log formats can drift and require adapter updates.
- Project paths are observed hints rather than a project registry.
- Graph output remains structured JSON; report and trends have standalone HTML
  views but no full-session graph visualization.
- HTML is not hosted, does not start a server or launch a browser, and does not
  render full transcripts. Calendar dates are observed timezone-naive dates.
- OpenCode, exports, MCP/query access, CI, PyPI publishing, and a GitHub Release
  are not included in the current dogfood baseline.
- Before 1.0, incompatible databases and artifacts may need explicit rebuilds.

## Development and documentation

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv build
```

Further reading:

- [Design and implementation details](docs/session-doctor-design.md)
- [Current Codex format validation](docs/codex-native-format-validation.md)
- [Changelog](CHANGELOG.md)
- [License](LICENSE)
