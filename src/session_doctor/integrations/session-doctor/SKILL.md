---
name: session-doctor
description: Capture, normalize, and inspect deterministic task episodes from local Codex, Claude Code, and Pi sessions.
license: MIT
compatibility: Requires session-doctor CLI version 0.1.0.
metadata:
  session-doctor-version: "0.1.0"
---

# Session Doctor

Use the public `session-doctor` CLI as the only interface to session data. Do
not read native transcripts or query DuckDB directly.

## Available Workflow

Check prerequisites and adapter roots:

```bash
session-doctor doctor
session-doctor adapters
session-doctor db init
session-doctor integrations path
```

Capture and normalize sessions locally:

```bash
session-doctor ingest --agent codex
session-doctor ingest --agent claude
session-doctor ingest --agent pi
session-doctor sessions list
```

Analyze one normalized native session into deterministic task episodes:

```bash
session-doctor analyze SESSION_ID
session-doctor analyze SESSION_ID --format json
session-doctor analyze SESSION_ID --snapshot-id SNAPSHOT_ID
session-doctor analyze SESSION_ID --projection-id PROJECTION_ID
```

The analysis output contains persisted event-anchored episodes, total entity
membership, boundary decisions, lifecycle provenance, and exact native
delegation status. Snapshot selection writes one explicit historical analysis;
projection selection is read-only. It does not contain v1 labels, risk scores,
aggregates, continuation/family inference, recommendations, or LLM judgments.

Inspect exact captured history or replay normalization when requested:

```bash
session-doctor snapshots list
session-doctor snapshots show SNAPSHOT_ID
session-doctor normalizations status SNAPSHOT_ID
session-doctor normalizations replay SNAPSHOT_ID
```

Snapshot replay and pruning are explicit write operations. Never add
`--overwrite` or `--force` without the user's informed authorization.

Evaluation packet export and judge import are offline workflows:

```bash
session-doctor evaluation export-boundaries NORMALIZATION_RUN_ID --output PACKET_DIR
session-doctor evaluation export-pilot --output PACKET_DIR
session-doctor evaluation import-judge --input ANNOTATION_JSON
```

Session Doctor does not call an LLM provider. Judge annotations are produced
outside the production analyzer and imported as immutable records.
Task-specific episode packet export remains unavailable until its owning rubric
and allowed-answer set are versioned.

## Temporarily Unavailable Commands

During the deterministic analysis v2 rebuild, these commands deliberately
fail and must not be invoked or emulated:

```text
summary
trends
projects list
report
graph
```

Their error is:

```text
<command> is unavailable during the deterministic analysis v2 rebuild; see docs/deterministic-analysis-v2-plan.md.
```

Do not fall back to old scores, classifications, report payloads, graphs,
direct SQL, or transcript inspection. Explain the temporary unavailability and
use only the available episode analysis and snapshot commands.
There is no v1 fallback.

## Privacy And Interpretation

- Treat snapshot bytes, routing envelopes, prompts, tool arguments, and model
  identities as private.
- Share judge-visible packet files only; routing remains in DuckDB.
- Preserve unknown, ambiguous, active, incomplete, provisional, delegated, and
  mixed-model states exactly.
- Do not convert ambiguity into a split or infer abandonment from silence.
- Do not compare models unless the future controlled-cohort contract says the
  episodes are eligible.
