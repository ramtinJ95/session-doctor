# Phase 11 Validation

Validated on 2026-07-12. Phase 11 adds typed visual projections, standalone
exact-session HTML reports, and standalone project-trends HTML dashboards.
All retained screenshots use repository synthetic fixtures. No copied-local
HTML, database, source log, path, identifier, message, or prose was retained.

## Automated contract coverage

The suite covers:

- schema-version-2 report JSON and bounded source-record-order sequences;
- complete daily top-level and sidechain calendars, including zero-date cells,
  weekly/monthly boundaries, and year boundaries;
- current, stale, missing/never-analyzed, unresolved-marker, null-rate, exact
  denominator, and family-exclusion states;
- output-path requirements, extension/parent/symlink validation, atomic
  replacement, and failed-write preservation;
- HTML escaping, CSP/offline resources, default message omission, bounded
  `--show-text`, redacted paths, and no database path;
- semantic/no-JavaScript fallbacks, print hooks, calendar controls, and cohort
  separation;
- bundled-skill wording for write confirmation, exact-path replacement, and
  separate evidence-text confirmation;
- wheel/sdist inclusion and execution from a clean installed-wheel environment.

The installed-wheel integration test builds the wheel, creates a clean virtual
environment, installs dependencies at the exact frozen `uv.lock` versions and
then installs the project wheel with `--no-deps`. Its subprocess environment
removes `PYTHONPATH` before it ingests and analyzes a synthetic fixture and
generates both HTML surfaces through the installed `session-doctor` executable.
It verifies concise path confirmations, no HTML on stdout, replacement of a
pre-existing destination, and no implicit artifact directory.

## Browser matrix

Functional browser review used generated synthetic-fixture documents and no
network access. Retained evidence and review notes:

- exact-session report: [`phase-11-report-screenshots/`](phase-11-report-screenshots/)
- project trends: [`phase-11-trends-screenshots/`](phase-11-trends-screenshots/)

| Surface | Coverage |
| --- | --- |
| Exact-session report | desktop light, mobile dark, expanded bounded evidence, no JavaScript, print media |
| Project trends | weekly desktop light, mobile dark, volume/risk calendar switch, empty state, no JavaScript, print media |

The browser checks verified readable semantic content without JavaScript, both
calendar alternatives remaining available without JavaScript, print-media
behavior, dark/mobile layout functionality, and zero network requests. They are
functional evidence, not pixel-stability tests.

## Three-adapter synthetic coverage

Repository fixtures exercise native Codex, Claude, and Pi sessions through
normalization, analysis, typed report/trend projections, and HTML rendering.
Coverage includes top-level and sidechain sessions, warning and stale-analysis
states, empty trends, bounded evidence, weekly/monthly windows, and calendar
boundaries. Retained examples contain synthetic fixture data only.

## Copied-local privacy-safe smoke validation

With explicit approval, one completed root session per adapter was copied into
a temporary directory and processed with the public CLI. `TemporaryDirectory`
removed the copied logs, DuckDB file, and generated HTML at completion; the
one-off script was then deleted. Only these structural results were retained:

| Check | Result |
| --- | ---: |
| Discoverable root candidates (Codex / Claude / Pi) | 70 / 43 / 129 |
| Copied sources / normalized sessions | 3 / 3 |
| Top-level / sidechain sessions | 3 / 0 |
| Current analysis rows (`phase6`) | 3 |
| Total normalized and analysis table rows | 7,568 |
| Exact-session reports generated | 3 |
| Weekly/monthly dashboards generated | 2 |
| Weekly/monthly calendar cells | 336 / 1,476 |
| Default message-text matches in HTML | 0 |
| Documents containing external HTTP(S) URLs | 0 |
| Database table counts unchanged by HTML rendering | yes |

No private content was printed or preserved. This validates adapter-native
shape handling and database-read-only rendering, not any qualitative judgment
about the sessions.

## Final gates

The release gate is:

```text
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q
uv build
```

Final result: Ruff formatting and lint passed, `ty` passed, all 317 tests passed,
and both the sdist and wheel built successfully. No release tag, PyPI upload,
GitHub Release, CI publication, or browser launch is part of Phase 11.
