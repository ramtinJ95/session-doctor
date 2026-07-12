# Phase 11 Plan: Standalone Visual Reports And Trend Dashboards

Status: complete; implementation and privacy-safe validation finished on
2026-07-12. See `docs/phase-11-validation.md`.

## Goal

Add polished, local, graphical reporting without turning the existing evidence
graph into a full-session node-link diagram.

Phase 11 adds two standalone HTML surfaces:

1. an exact-session diagnostic report with score explanations, a compact
   session-sequence view, evidence drilldowns, ending state, and historical
   recurrence; and
2. a project-trends dashboard with contribution-calendar views, score and risk
   trends, analysis coverage, cohort separation, and recurring patterns.

The visual surfaces must answer diagnostic questions already supported by
persisted normalized and analysis data. They must not imply chronology,
causality, precision, or project identity beyond the existing contracts.

HTML is a presentation of typed report/trend projections, not a second analysis
system. The browser performs no queries, analysis, network requests, telemetry,
or persistence.

## Grilled Decisions

The following decisions are load-bearing:

1. Deliver both the exact-session report and project-trends dashboard in Phase
   11, but land them in separate implementation PRs behind shared typed
   contracts.
2. Generate custom semantic HTML, CSS, and SVG. Do not add Plotly, Altair,
   Vega-Lite, a frontend framework, or a chart runtime.
3. Use progressive enhancement. Every report remains readable without
   JavaScript; small first-party inline JavaScript may add metric toggles,
   filters, tooltips, and disclosure controls.
4. HTML output requires an explicit `--output PATH`. HTML is never emitted to
   stdout and is never written to an implicit default location.
5. The named HTML file is always replaced. Replacement must be atomic so a
   rendering or write failure cannot leave a partially written report.
6. The output parent directory must already exist. The command does not create
   directories, launch a browser, or write any sibling assets.
7. `--show-text` retains the current disclosure scope: only displayed bounded
   evidence messages may contain text. Timeline/sequence entries remain
   metadata-only.
8. The exact-session visualization uses a compact activity-density strip plus
   exact diagnostic-evidence markers. It does not draw every raw event or every
   graph node.
9. The contribution calendar shows neutral session volume by default and may
   switch to risky-session rate. Risk views always expose current-analysis
   coverage and sample counts.
10. `analyze` remains the producer of persisted analysis rows and does not
    render HTML. `report` owns exact-session HTML; successful analysis output
    may point users to the report command.
11. Timeline and calendar data are typed public projections included in JSON,
    not private renderer models or direct renderer queries against DuckDB.
12. Support automatic light/dark themes through system preference and a
    dedicated print stylesheet. Do not add selectable or persisted themes.
13. Validate semantics and document structure automatically. Validate layout
    with reviewed browser screenshots rather than brittle cross-platform pixel
    assertions.

## Starting Point

At Phase 11 start:

- `report SESSION_ID` emits terminal, Markdown, or schema-versioned JSON;
- `trends` emits terminal or JSON for week/month buckets;
- report scores already expose component values, weights, contributions, and
  source event IDs;
- report classifications, bounded evidence, ending state, review actions,
  limitations, and project recurrence are typed;
- trend models already expose top-level and sidechain cohorts, score averages,
  classification rates, risk rates, guarded judgments, analysis compatibility,
  agent observations, and recurring patterns;
- graph projection is complete structured JSON but is deliberately unsuitable
  as the default full-session visualization;
- report and trend commands are database-read-only and currently write no
  output files;
- the package has no plotting, browser, templating, or frontend dependency.

## Scope

### In Scope

- typed exact-session sequence/timeline projection
- typed daily calendar projection aligned to the selected trend window
- public JSON representation of the new visual data
- standalone exact-session HTML report
- standalone project-trends HTML dashboard
- custom dependency-free HTML/CSS/SVG rendering
- minimal progressive-enhancement JavaScript
- required explicit HTML output paths and atomic replacement
- light, dark, responsive, accessible, and print presentation
- explicit stale/missing-analysis and empty-data states
- privacy and HTML-injection hardening
- structural, semantic, cross-adapter, and reviewed screenshot validation
- README, design, CLI help, bundled Agent Skill, and limitation updates

### Out Of Scope

- full-session graph/DAG rendering
- Graphviz, NetworkX, Plotly, Altair, Vega, Matplotlib, or image-generation
  dependencies
- PNG, SVG-only, or PDF export commands
- a hosted dashboard, local HTTP server, or browser-launch command
- CDNs, remote fonts, remote icons, network requests, telemetry, or analytics
- JavaScript-first rendering or client-side analysis
- all-message transcript rendering
- message text outside displayed bounded evidence under explicit `--show-text`
- new classifiers, scores, causal edges, or inference rules
- automatic analysis from `report` or `trends`
- HTML output from `analyze`
- a visual `summary` or `graph` format
- merging top-level and sidechain evidence into one diagnostic cohort
- inferred repository roots, user time zones, goals, outcomes, or causal
  execution chains
- selectable themes or browser-storage preferences
- exact pixel-regression tests

## Product Contract

### Exact-Session Report

The exact-session page presents sections in this order:

1. **Header and status**
   - session ID, agent, model, scope, observed project hint, start/end, and
     duration when both timestamps exist;
   - current/stale/missing analysis status and explicit recovery action;
   - privacy status, including whether bounded evidence text is present.
2. **Diagnostic overview**
   - summary counts;
   - primary classifications with score, confidence, evidence summary, and
     evidence count;
   - ending-state status without inventing a resolved/unresolved claim when the
     source report says unavailable.
3. **Scores and contributions**
   - horizontal 0..1 score bars for the five existing scores;
   - exact numeric values and sample/evidence availability;
   - component values, weights, and contributions shown as explanatory detail;
   - no gauge, dial, grade, percentile, or unimplemented threshold language.
4. **Session sequence**
   - an activity-density strip over source record order;
   - lanes/composition for messages, tools, commands, files, failures, and
     warnings where supported;
   - exact markers for evidence-linked events;
   - an explicit label that horizontal position means observed record order,
     not measured elapsed time or causality;
   - timestamps shown only as metadata when available.
5. **Evidence**
   - repeated requests, corrections, frustration, ambiguity, scope boundaries,
     stop/pause markers, failed commands, tool failures, repeated failure
     groups, repeated file edits, and classification references;
   - native HTML `<details>` disclosure where a section has rows;
   - displayed/total/omitted counts retained exactly.
6. **Ending, recurrence, and actions**
   - ending evidence and unresolved references;
   - historical failed-command, failed-tool, and problematic-file recurrence;
   - observations, review actions, and limitations with their evidence IDs.

The page must not duplicate every raw event as visible prose or SVG. It must not
consume graph JSON to reconstruct report semantics.

### Project-Trends Dashboard

The trends page presents:

1. **Scope and coverage**
   - exact selected project/agent/bucket/period filters;
   - window start/end and latest matching session;
   - matching, windowed, outside-window, and untimed session counts;
   - current, stale, and never-analyzed counts.
2. **Contribution calendars**
   - complete observed-date cells from the selected trend window;
   - session count as the default color metric;
   - risky-session rate as an optional enhanced view;
   - current-analysis coverage, risky-session numerator, and current-analysis
     denominator in labels/tooltips;
   - top-level and sidechain cohorts remain separately selectable/presented and
     are never silently pooled into one diagnostic rate.
3. **Trend charts**
   - session volume and analysis coverage;
   - the five existing score averages with sample counts;
   - risky-session rate with analyzed-session denominator;
   - week/month bucket boundaries exactly matching the typed trend report.
4. **Judgments and distributions**
   - existing guarded trend judgments and their insufficiency reasons;
   - classification counts/rates;
   - agent observations without ranking agents or implying causality.
5. **Recurring patterns**
   - failed commands, failed tools, and problematic files with event, session,
     root-family, cohort, active-bucket, and recency evidence.

The calendar uses the timezone-naive observed datetimes already stored by the
project. It labels dates as observed dates and performs no inferred local/UTC
conversion.

## Typed Visual Data Contracts

### Session Sequence

Extend `SessionReport` with a typed `sequence` section and increment its schema
version because the public JSON shape changes.

The sequence contract must expose:

- ordering basis (`source_record_order`);
- first/last resolved source record index;
- total resolved and unresolved visual activities;
- deterministic bins covering the selected session's source record range;
- per-bin counts by a fixed activity vocabulary;
- evidence markers with category, evidence ID, source event ID, resolved record
  index, and optional observed timestamp;
- unresolved marker counts by category.

The fixed activity vocabulary should be deliberately small:

```text
user_message
assistant_message
tool_call
tool_result
tool_failure
command_success
command_failure
command_unknown
file_activity
parse_warning
```

Projection rules:

- derive activities from normalized entities and exact source-event IDs;
- never infer links from timestamp proximity or raw-event co-location;
- assign one entity to one activity category; for example, a failed tool result
  is `tool_failure`, not both `tool_result` and `tool_failure`;
- resolve parse warnings through the existing exact `(source_id, record_index)`
  rule and count null, unmatched, or ambiguous warning positions as unresolved;
- use deterministic fixed-maximum binning so output size is bounded while empty
  spans remain visible;
- retain exact evidence markers independently of density bins;
- count missing/unresolved source references rather than dropping them quietly;
- do not include message text, command output, tool output, arguments, diffs,
  native hashes, private paths, or generic metadata.

The terminal and Markdown report renderers need not draw the sequence, but JSON
must expose it and existing textual formats should summarize its resolved and
unresolved activity counts.

### Daily Calendar

Extend the typed trend report with daily calendar cohorts. Each daily cell must
contain:

- observed date and half-open datetime interval;
- session count;
- current, stale, and never-analyzed counts;
- risky-session count;
- current-analysis coverage;
- risky-session rate using current analyzed sessions as the denominator.

Calendar requirements:

- include zero-session dates so layout does not infer missing data;
- align exactly to the existing selected trend window, independent of whether
  the analytical bucket is week or month;
- derive top-level and sidechain cells from the same already-filtered session
  rows used by trend cohorts;
- use the existing risk definition and analyzer-version compatibility rules;
- represent unavailable rates as `null`, never zero;
- preserve untimed sessions only in scope counts, not invented calendar dates;
- expose calendar cells in trend JSON so HTML contains no hidden aggregation
  algorithm.

## HTML Rendering Architecture

### Modules

Add a small presentation package rather than expanding the existing terminal
renderer files indefinitely:

```text
src/session_doctor/html/
    __init__.py
    document.py
    components.py
    charts.py
    report.py
    trends.py
    assets.py
```

Responsibilities:

- `document.py`: escaped document shell, metadata, CSP, theme/print hooks;
- `components.py`: semantic cards, tables, status badges, empty states,
  disclosure sections, and legends;
- `charts.py`: deterministic SVG primitives and accessible textual fallbacks;
- `report.py`: composition from `SessionReport` only;
- `trends.py`: composition from the typed trend projection only;
- `assets.py`: one inline stylesheet and minimal first-party enhancement script.

Do not introduce a general-purpose template engine. Small explicit render
functions keep escaping and allowed markup reviewable. Reuse components between
the two pages; do not create separate style systems.

### Security And Privacy

Every dynamic string must be HTML-escaped at the final text/attribute boundary.
No user-controlled or persisted string may be concatenated into raw markup,
CSS, or JavaScript.

Each document must include a restrictive policy suitable for a self-contained
local file:

- no network connections;
- no remote scripts, styles, fonts, images, frames, or form submissions;
- inline first-party CSS/SVG and the fixed enhancement script only;
- no report payload copied into executable JavaScript;
- no `localStorage`, cookies, IndexedDB, service workers, or telemetry;
- no external links containing private report values;
- no source database path in the document.

Interactive behavior should use fixed DOM data attributes and existing rendered
content. JavaScript must not create a second semantic projection.

### Accessibility And Responsive Behavior

- use semantic landmarks and heading order;
- include a skip link and visible keyboard focus;
- expose SVG charts with concise accessible names/descriptions;
- retain tables or text summaries for every chart;
- do not rely on color alone for status, cohort, or risk meaning;
- meet WCAG AA contrast for both system themes;
- respect `prefers-reduced-motion` and use no necessary animation;
- make evidence disclosures keyboard operable through native controls;
- support narrow screens without hiding evidence or requiring horizontal page
  scrolling; chart regions may scroll internally when unavoidable;
- provide print CSS that removes controls, expands relevant disclosures where
  practical, preserves legends, and avoids clipped charts.

### Visual Semantics

Use a shared token system for spacing, typography, borders, surfaces, and
semantic colors. Use system fonts only.

Colors must have stable meanings:

- neutral activity volume is not colored as success/failure;
- negative diagnostic evidence uses a warning/risk palette;
- stale/missing analysis uses an unavailable palette, not a low-risk palette;
- positive/resolution labels are distinct from low scores;
- score magnitude is shown continuously without converting it to a letter grade.

Dark mode uses `prefers-color-scheme`; it is not a separate saved user setting.

## CLI And File Contract

Extend only `report` and `trends`:

```text
session-doctor report SESSION_ID --format html --output report.html
session-doctor trends --project PATH --format html --output trends.html
```

Rules:

- `--output` is required when `--format html`;
- `--output` is rejected for terminal, Markdown, or JSON output so ignored
  options cannot create false expectations;
- require an `.html` or `.htm` suffix;
- require the parent directory to exist and be writable;
- reject a destination that is a directory or unsupported filesystem object;
- generate the complete UTF-8 document before touching the destination;
- write to a temporary sibling and atomically replace the named destination;
- always replace an existing regular destination file without prompting;
- clean up the temporary sibling after a failed replacement;
- never create an artifact directory, sidecar asset, cache, or database row;
- never launch a browser;
- return a nonzero exit with a stable, privacy-safe error when rendering or
  replacement fails;
- on success, print a concise confirmation rather than the HTML contents.

The commands remain database-read-only but become explicit file-writing
commands in HTML mode. Documentation and the bundled Agent Skill must classify
HTML mode as a write and require confirmation with the exact output path.

`analyze` does not gain HTML flags. Its successful single-session output may
show the command needed to generate a report, but must not write one.

## Empty, Partial, And Error States

Standalone HTML must still be generated successfully when:

- analysis is stale or missing;
- the session has no resolved timeline activities;
- timestamps are absent;
- classifications or evidence sections are empty;
- the selected trend window has no current analysis;
- risk rates or score samples are unavailable;
- a cohort has zero sessions;
- project recurrence is unavailable.

Unavailable data must remain unavailable. Do not render it as zero, green,
healthy, or complete. Every partial page must preserve the typed action/reason
that explains recovery or insufficiency.

CLI validation errors occur before snapshot/trend loading where practical.
Rendering errors must not alter an existing output file.

## Test And Validation Plan

### Typed Projection Tests

- deterministic sequence bins and evidence-marker ordering;
- activity categories are mutually exclusive and counts reconcile;
- exact source-event resolution and explicit unresolved counts;
- no private fields or undisclosed message text in sequence JSON;
- report schema-version change and stable top-level shape;
- complete daily intervals including zero-session dates;
- calendar alignment with week/month trend windows;
- current/stale/missing coverage and null-rate semantics;
- top-level/sidechain separation and untimed-session exclusion;
- cross-adapter fixtures for Codex, Claude, and Pi.

### HTML Contract Tests

- deterministic output for the same typed input;
- valid document landmarks, heading order, metadata, CSP, and one trailing
  document close;
- complete escaping of text and attributes using hostile fixture values;
- no remote URLs, network-capable assets, private database paths, or generic
  serialized payload blobs;
- default reports contain no message text;
- `--show-text` contains only displayed bounded evidence text;
- stale/missing analysis and all empty states remain explicit;
- every SVG has an accessible label/description and a textual fallback;
- report/trend pages remain semantically complete when the enhancement script
  is removed;
- light/dark/print style hooks and reduced-motion rules are present.

### CLI And Filesystem Tests

- HTML requires `--output` and a supported suffix;
- non-HTML formats reject `--output`;
- missing parent, directory target, and unwritable target fail clearly;
- existing files are replaced as approved;
- a forced render/write failure leaves the existing file byte-for-byte intact;
- no database rows, default artifacts, caches, or sibling assets are written;
- successful output is a single self-contained UTF-8 HTML file;
- report agent mismatch, missing session, schema mismatch, and filter validation
  retain their current precedence.

### Browser Review Matrix

Generate privacy-safe fixture reports and reviewed screenshots for:

- current high-friction exact session;
- healthy/low-evidence exact session;
- stale and missing analysis;
- long session with dense activity and bounded evidence;
- top-level and sidechain sessions for all three adapters;
- populated weekly trends;
- populated monthly trends spanning year boundaries;
- sparse/empty trends and low analysis coverage.

Review each representative surface at:

- desktop light and dark;
- narrow/mobile viewport;
- print preview;
- JavaScript enabled and disabled;
- keyboard-only navigation.

Retain screenshots and structural counts only when validation uses copied local
data. Never retain private source content, paths, commands, or message text.
Document findings and fixes in a Phase 11 validation record; pixel equality is
not an automated gate.

### Quality Gate

Each PR must pass:

```text
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
git diff --check
uv build
```

Final validation also clean-installs the wheel and generates both HTML surfaces
through the installed CLI.

## Four-PR Delivery

### PR 1: Typed Visual Projections

Purpose: establish semantic contracts without adding HTML presentation.

Changes:

- add exact-session sequence models and deterministic projection;
- add daily calendar cohort models and projection;
- expose both through JSON;
- increment the exact-session report schema version;
- summarize sequence availability in terminal/Markdown without graphical
  rendering;
- add projection, privacy, compatibility, empty-state, and cross-adapter tests;
- document the changed machine-readable contracts.

Review boundary:

- no HTML, CSS, SVG, JavaScript, or filesystem-output behavior;
- reviewers can decide whether every future visual claim is supported by typed
  source data.

Acceptance:

- sequence and calendar projections are deterministic and bounded;
- counts reconcile and unresolved references are explicit;
- risk denominators and cohort separation are correct;
- JSON remains privacy-safe and renderer-independent.

### PR 2: Exact-Session Standalone HTML

Purpose: establish the shared visual system through the richer diagnostic page.

Changes:

- add the shared HTML document/components/charts/assets package as required by
  the report, without speculative unused primitives;
- implement exact-session HTML composition;
- add `report --format html --output PATH` and atomic replacement;
- implement light/dark, responsive, print, accessibility, and progressive
  enhancement behavior;
- add HTML escaping/privacy/structure and filesystem tests;
- add reviewed report screenshots and fix findings.

Review boundary:

- no trend HTML yet;
- existing terminal, Markdown, and JSON behavior changes only where PR 1's
  explicit schema contract requires it.

Acceptance:

- one self-contained report file answers what happened diagnostically, where
  evidence appears in source order, and what should be reviewed next;
- the page is useful without JavaScript;
- default and `--show-text` privacy contracts match existing behavior;
- failed writes never damage an existing destination.

### PR 3: Project-Trends Standalone HTML

Purpose: complete the planned visual product with a Tokscale-inspired but
diagnostic-specific historical dashboard.

Changes:

- reuse and extend only the shared components needed by trends;
- implement contribution calendars, trend SVGs, coverage displays, judgments,
  cohort views, agent observations, and recurring-pattern sections;
- add `trends --format html --output PATH` with the same file contract;
- implement the session-volume/risk metric toggle as progressive enhancement;
- add trend HTML, calendar, sparse-data, responsive, and filesystem tests;
- add reviewed trend screenshots and fix findings.

Review boundary:

- no new analysis metrics or altered judgment thresholds;
- visuals consume only the typed trend projection from PR 1.

Acceptance:

- users can distinguish volume, analyzed coverage, and risk rather than seeing
  one overloaded heatmap;
- top-level and sidechain cohorts remain explicit;
- unavailable/sparse data cannot appear healthy by default;
- report and trends pages share one coherent visual language.

### PR 4: Integration, Documentation, And Native Validation

Purpose: finish the feature as a product rather than leaving isolated renderers.

Changes:

- update README examples, CLI reference, privacy guidance, current limitations,
  and report/trend descriptions;
- reconcile `docs/session-doctor-design.md` with the HTML architecture and file
  side effects;
- update the bundled Agent Skill so HTML generation is treated as a confirmed
  write with an explicit output path and replacement warning;
- run three-adapter and copied-local visual validation;
- verify installed-wheel self-contained generation;
- write `docs/phase-11-validation.md` with structural evidence, screenshot
  matrix, findings, fixes, and retained privacy-safe counts;
- remove obsolete claims that reports/trends never write files, narrowing them
  to database-read-only behavior.

Review boundary:

- no new visual feature unless required to fix a documented validation finding;
- no release/tag step unless separately requested.

Acceptance:

- public documentation and integration guidance match exact CLI behavior;
- copied-local validation retains no private content;
- both dashboards are coherent across adapters, themes, viewport sizes, print,
  and missing-data states;
- all quality gates and clean-install smoke tests pass.

## Acceptance Criteria

Phase 11 is complete when:

- exact-session and trend HTML are each one standalone offline file;
- HTML requires an explicit existing-parent output path and atomically replaces
  the named file;
- typed JSON exposes all visualized timeline/calendar semantics;
- no renderer queries DuckDB or reconstructs analysis rules;
- the exact-session sequence remains bounded and does not become a graph or
  transcript replay;
- calendar volume, risk rate, analysis coverage, and denominators are distinct;
- scores and classifications preserve heuristic uncertainty and evidence;
- top-level and sidechain cohorts never mix implicitly;
- default HTML contains no message text;
- `--show-text` discloses only displayed bounded evidence messages;
- no report performs network access or browser persistence;
- HTML remains useful without JavaScript and accessible without color alone;
- automatic light/dark and print presentation pass reviewed validation;
- existing terminal/Markdown/JSON users receive explicit contract changes only;
- no database row, cache, default artifact directory, or sidecar asset is
  created by HTML generation;
- Codex, Claude, and Pi fixture/native flows pass;
- documentation and the bundled Agent Skill match the new write contract;
- Ruff, typing, tests, build, clean install, and diff checks pass.

## Explicitly Deferred

- graphical graph projection
- visual summary command
- browser launch and local web server
- image/PDF export
- selectable themes
- frontend framework or reusable external chart package
- hosted sharing, cloud sync, and telemetry
- all-message transcript views
- client-side DuckDB or arbitrary report queries
- visual editor, annotations, or saved dashboard preferences
- pixel-diff CI
- release publication or tag

## Grilling Status

Resolved interactively before implementation:

- both exact-session and project-trends visuals are in the phase;
- custom HTML/CSS/SVG is preferred over chart libraries;
- progressive enhancement is required;
- HTML requires an explicit output path;
- existing output is always replaced atomically;
- missing parent directories fail rather than being created;
- message disclosure remains bounded evidence only;
- the session view uses density plus evidence markers;
- contribution calendars default to volume with an optional risk view;
- `analyze` does not own HTML rendering;
- visual data is public typed JSON;
- validation combines structural tests with reviewed screenshots;
- automatic light/dark and print support ship together.
