# ruff: noqa: E501

STYLES = r"""
:root {
  color-scheme: light dark;
  --bg: #f9f9f7;
  --surface: #fcfcfb;
  --surface-alt: #f0efec;
  --text: #0b0b0b;
  --ink-2: #52514e;
  --ink-3: #898781;
  --grid-line: #e1e0d9;
  --axis-line: #c3c2b7;
  --border: rgb(11 11 11 / 10%);
  --series-1: #2a78d6;
  --series-2: #1baf7a;
  --series-3: #eda100;
  --series-4: #4a3aa7;
  --series-5: #e34948;
  --accent: #2a78d6;
  --accent-ink: #1c5cab;
  --risk: #d03b3b;
  --risk-ink: #a72f2f;
  --positive: #0ca30c;
  --positive-ink: #006300;
  --deemph: #b9b7af;
  --focus: #1c5cab;
  --shadow: 0 1px 2px rgb(11 11 11 / 5%);
  --radius: .55rem;
  --space-1: .25rem;
  --space-2: .5rem;
  --space-3: .75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2.25rem;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d0d0d;
    --surface: #1a1a19;
    --surface-alt: #242422;
    --text: #ffffff;
    --ink-2: #c3c2b7;
    --ink-3: #898781;
    --grid-line: #2c2c2a;
    --axis-line: #383835;
    --border: rgb(255 255 255 / 10%);
    --series-1: #3987e5;
    --series-2: #199e70;
    --series-3: #c98500;
    --series-4: #9085e9;
    --series-5: #e66767;
    --accent: #3987e5;
    --accent-ink: #86b6ef;
    --risk: #e66767;
    --risk-ink: #f0a3a3;
    --positive: #0ca30c;
    --positive-ink: #6fce6f;
    --deemph: #4b4b48;
    --focus: #86b6ef;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--text); line-height: 1.55; font-size: .95rem; }
a { color: var(--accent-ink); }
a:focus-visible, button:focus-visible, summary:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.skip-link { position: absolute; left: var(--space-4); top: -5rem; z-index: 10; padding: var(--space-3); background: var(--surface); border: 2px solid var(--focus); border-radius: var(--radius); }
.skip-link:focus { top: var(--space-4); }
.page { width: min(76rem, calc(100% - 2.5rem)); margin: 0 auto; padding: var(--space-6) 0 4rem; }
header[role="banner"] { margin-bottom: var(--space-5); }
h1, h2, h3, h4 { line-height: 1.25; letter-spacing: -.01em; }
h1 { margin: 0 0 var(--space-2); font-size: clamp(1.5rem, 3vw, 2.1rem); font-weight: 750; overflow-wrap: anywhere; }
.lede { margin: 0 0 var(--space-1); color: var(--ink-3); font-size: .78rem; font-weight: 650; letter-spacing: .09em; text-transform: uppercase; }
.session-key { margin: 0 0 var(--space-3); color: var(--ink-2); overflow-wrap: anywhere; }
.session-key code { font-size: .85rem; }
h2 { margin: 0 0 var(--space-2); font-size: 1.3rem; font-weight: 700; }
h3 { margin: var(--space-5) 0 var(--space-3); font-size: 1.02rem; font-weight: 650; }
.card > h3, .card h3:first-child { margin-top: 0; }
.muted { color: var(--ink-2); }
.section { margin-block: var(--space-6); scroll-margin-top: var(--space-4); }
main > .section + .section { border-top: 1px solid var(--border); padding-top: var(--space-5); }
.section > h2 + .muted { margin-top: 0; max-width: 62rem; }
footer.section { color: var(--ink-3); font-size: .85rem; border-top: 1px solid var(--border); padding-top: var(--space-4); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); gap: var(--space-3); }
.grid.kpi { grid-template-columns: repeat(auto-fit, minmax(min(100%, 8.5rem), 1fr)); }
.card { min-width: 0; padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow); overflow-wrap: anywhere; }
.card > :last-child { margin-bottom: 0; }
.status-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
.badge { display: inline-flex; align-items: center; gap: .3rem; border-radius: 999px; padding: .14rem .6rem; font-size: .74rem; font-weight: 650; letter-spacing: .01em; }
.badge.current, .badge.available { color: var(--positive-ink); background: color-mix(in srgb, var(--positive) 13%, var(--surface)); }
.badge.neutral { color: var(--accent-ink); background: color-mix(in srgb, var(--accent) 13%, var(--surface)); }
.badge.risk { color: var(--risk-ink); background: color-mix(in srgb, var(--risk) 13%, var(--surface)); }
.badge.stale, .badge.missing, .badge.unavailable { color: var(--ink-2); background: var(--surface-alt); }
dl.meta { display: grid; grid-template-columns: minmax(8rem, auto) minmax(0, 1fr); gap: var(--space-1) var(--space-4); margin: 0; font-size: .88rem; }
dl.meta dt { color: var(--ink-2); font-weight: 500; }
dl.meta dd { margin: 0; overflow-wrap: anywhere; }
.stat { font-size: 1.7rem; font-weight: 650; line-height: 1.15; }
.stat-label { color: var(--ink-2); font-size: .8rem; }
.score-row { display: grid; grid-template-columns: minmax(11rem, 1fr) minmax(12rem, 3fr) 4rem; align-items: center; gap: var(--space-3); margin-block: var(--space-4); }
.score-name { font-weight: 600; font-size: .92rem; }
progress { width: 100%; height: .45rem; border: 0; border-radius: 999px; overflow: hidden; background: color-mix(in srgb, var(--series-1) 15%, var(--surface)); }
progress::-webkit-progress-bar { background: color-mix(in srgb, var(--series-1) 15%, var(--surface)); }
progress::-webkit-progress-value { background: var(--series-1); border-radius: 999px; }
progress::-moz-progress-bar { background: var(--series-1); border-radius: 999px; }
.score-value { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; font-size: .92rem; }
.chart-scroll { overflow-x: auto; padding-bottom: var(--space-2); }
svg text { font-family: inherit; }
.sequence-chart { display: block; min-width: 48rem; width: 100%; height: auto; color: var(--text); }
.sequence-chart .lane-band { fill: var(--surface-alt); opacity: .5; }
.sequence-chart .gridline { stroke: var(--grid-line); stroke-width: 1; }
.sequence-chart .axis { stroke: var(--axis-line); stroke-width: 1; fill: none; }
.sequence-chart .lane-label { fill: var(--ink-2); font-size: 11.5px; }
.sequence-chart .tick-label { fill: var(--ink-3); font-size: 11px; }
.sequence-chart .activity { fill: var(--series-1); }
.sequence-chart .activity-risk { fill: var(--risk); }
.sequence-chart line.marker { stroke-width: 1; stroke-dasharray: 2 3; }
.sequence-chart circle.marker { stroke-width: 1.5; fill: var(--surface); }
.sequence-chart .marker-neutral { stroke: var(--ink-3); }
.sequence-chart .marker-risk { stroke: var(--risk); }
.legend { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); padding: 0; margin: var(--space-2) 0; list-style: none; color: var(--ink-2); font-size: .82rem; }
.legend li { display: inline-flex; align-items: center; gap: .4rem; }
.legend-key { display: inline-block; width: .7rem; height: .7rem; border-radius: 2px; background: var(--c, var(--series-1)); }
.legend-key.risk { --c: var(--risk); }
.legend-key.rest { --c: var(--deemph); }
.legend-key.evidence { width: 0; height: .85rem; background: none; border-left: 2px dashed var(--ink-3); border-radius: 0; }
.series-1 { --c: var(--series-1); }
.series-2 { --c: var(--series-2); }
.series-3 { --c: var(--series-3); }
.series-4 { --c: var(--series-4); }
.series-5 { --c: var(--series-5); }
.trend-chart { display: block; min-width: 48rem; width: 100%; height: auto; }
.trend-chart .grid-h { stroke: var(--grid-line); stroke-width: 1; }
.trend-chart .axis { stroke: var(--axis-line); stroke-width: 1; fill: none; }
.trend-chart .tick-label { fill: var(--ink-3); font-size: 11px; }
.trend-chart .volume { fill: var(--series-1); }
.trend-chart .volume-rest { fill: var(--deemph); }
.trend-chart .risk-bar { fill: var(--risk); }
.trend-chart .series-line { fill: none; stroke: var(--c, var(--series-1)); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.trend-chart .series-dot { fill: var(--c, var(--series-1)); stroke: var(--surface); stroke-width: 2; }
.calendar { width: max-content; max-width: 100%; }
.calendar-wrap { overflow-x: auto; padding-bottom: var(--space-2); }
.calendar-months { position: relative; height: 1.05rem; margin-left: 2.1rem; font-size: .7rem; color: var(--ink-3); }
.calendar-months span { position: absolute; top: 0; }
.calendar-body { display: flex; }
.calendar-weekdays { display: grid; grid-template-rows: repeat(7, .8rem); gap: .2rem; width: 2.1rem; padding-right: .35rem; font-size: .62rem; color: var(--ink-3); text-align: right; }
.calendar-grid { display: grid; grid-template-rows: repeat(7, .8rem); grid-auto-flow: column; grid-auto-columns: .8rem; gap: .2rem; width: max-content; margin: 0; padding: 0; list-style: none; }
.calendar-cell, .legend-cell { width: .8rem; height: .8rem; border-radius: 2px; background: var(--surface-alt); }
.legend-cell { display: inline-block; vertical-align: -.1rem; }
.calendar-placeholder { background: transparent; }
.calendar-cell.level-1, .legend-cell.level-1 { background: color-mix(in srgb, var(--series-1) 25%, var(--surface)); }
.calendar-cell.level-2, .legend-cell.level-2 { background: color-mix(in srgb, var(--series-1) 45%, var(--surface)); }
.calendar-cell.level-3, .legend-cell.level-3 { background: color-mix(in srgb, var(--series-1) 68%, var(--surface)); }
.calendar-cell.level-4, .legend-cell.level-4 { background: color-mix(in srgb, var(--series-1) 92%, var(--surface)); }
.calendar-cell.risk-1, .legend-cell.risk-1 { background: color-mix(in srgb, var(--risk) 25%, var(--surface)); }
.calendar-cell.risk-2, .legend-cell.risk-2 { background: color-mix(in srgb, var(--risk) 45%, var(--surface)); }
.calendar-cell.risk-3, .legend-cell.risk-3 { background: color-mix(in srgb, var(--risk) 68%, var(--surface)); }
.calendar-cell.risk-4, .legend-cell.risk-4 { background: color-mix(in srgb, var(--risk) 92%, var(--surface)); }
.calendar-cell.unavailable, .legend-cell.unavailable { background: var(--surface); border: 1px dashed var(--axis-line); }
.calendar-legend { display: flex; flex-wrap: wrap; align-items: center; gap: .35rem; margin: var(--space-2) 0 0; color: var(--ink-3); font-size: .74rem; }
.calendar-legend .gap { width: var(--space-2); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
caption { caption-side: top; padding-bottom: var(--space-2); color: var(--ink-3); font-size: .78rem; text-align: left; }
th, td { padding: .45rem var(--space-3); border-bottom: 1px solid var(--grid-line); text-align: left; vertical-align: top; overflow-wrap: anywhere; }
td { font-variant-numeric: tabular-nums; }
th { color: var(--ink-2); font-weight: 600; font-size: .74rem; letter-spacing: .04em; text-transform: uppercase; }
tbody tr:last-child td { border-bottom: 0; }
code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .88em; overflow-wrap: anywhere; }
details { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); margin-block: var(--space-3); }
summary { cursor: pointer; padding: var(--space-3) var(--space-4); font-weight: 600; font-size: .92rem; color: var(--ink-2); }
details[open] > summary { color: var(--text); border-bottom: 1px solid var(--border); }
details > .details-body { padding: var(--space-3) var(--space-4) var(--space-4); overflow-wrap: anywhere; }
.empty { padding: var(--space-3) var(--space-4); border: 1px dashed var(--axis-line); border-radius: var(--radius); color: var(--ink-2); font-size: .88rem; }
.evidence-empty { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2) var(--space-3); margin-block: var(--space-3); padding: var(--space-2) var(--space-4); border: 1px dashed var(--border); border-radius: var(--radius); color: var(--ink-2); font-size: .88rem; }
.evidence-empty strong { color: var(--ink-2); font-weight: 600; }
.notice { margin-block: var(--space-3); border-left: 3px solid var(--axis-line); border-radius: 0 var(--radius) var(--radius) 0; padding: var(--space-2) var(--space-4); background: var(--surface-alt); font-size: .9rem; }
.notice.risk { border-color: var(--risk); background: color-mix(in srgb, var(--risk) 9%, var(--surface)); }
.controls { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-3); }
.controls[hidden] { display: none; }
button { border: 1px solid var(--border); border-radius: .45rem; padding: .4rem .8rem; color: var(--ink-2); background: var(--surface); font: inherit; font-size: .85rem; font-weight: 600; cursor: pointer; }
button:hover { background: var(--surface-alt); }
button[aria-pressed="true"] { color: var(--accent-ink); border-color: var(--accent-ink); background: color-mix(in srgb, var(--accent) 10%, var(--surface)); }
ul.clean { padding-left: 1.2rem; }
ul.clean li { margin-block: var(--space-2); }
@media (max-width: 42rem) {
  .page { width: min(100% - 1.25rem, 76rem); padding-top: var(--space-4); }
  .card { padding: var(--space-3); }
  dl.meta { grid-template-columns: minmax(7rem, 42%) minmax(0, 1fr); }
  .score-row { grid-template-columns: 1fr auto; }
  .score-row progress { grid-column: 1 / -1; grid-row: 2; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
@media print {
  :root { color-scheme: light; --bg: #fff; --surface: #fff; --surface-alt: #f3f3f2; --text: #000; --ink-2: #333; --ink-3: #555; --grid-line: #ddd; --axis-line: #999; --border: #999; --accent-ink: #174d91; --risk: #8a2413; --risk-ink: #8a2413; --positive-ink: #1d6337; --deemph: #bbb; --shadow: none; }
  @page { margin: 1.2cm; }
  body { background: #fff; font-size: 10pt; }
  .page { width: 100%; padding: 0; }
  .skip-link, .controls, script { display: none !important; }
  .section, .card, details { break-inside: avoid; }
  details:not([open]) > .details-body { display: block !important; }
  details:not([open]) > summary { border-bottom: 1px solid var(--border); }
  .chart-scroll { overflow: visible; }
  .calendar-wrap { overflow: visible; }
  [data-calendar-view][hidden] { display: block !important; }
  .sequence-chart { min-width: 0; }
  .trend-chart { min-width: 0; }
  a { color: inherit; text-decoration: none; }
}
"""

SCRIPT = r"""
(() => {
  document.documentElement.classList.add('js');
  const controls = document.querySelector('[data-disclosure-controls]');
  if (controls) {
    controls.hidden = false;
    controls.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-disclosure-action]');
      if (!button) return;
      const open = button.dataset.disclosureAction === 'open';
      document.querySelectorAll('main details').forEach((detail) => { detail.open = open; });
    });
  }
  document.querySelectorAll('[data-calendar-controls]').forEach((calendarControls) => {
    calendarControls.hidden = false;
    document.querySelectorAll('[data-calendar-view]').forEach((view) => {
      view.hidden = view.dataset.calendarView !== 'volume';
    });
    calendarControls.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-calendar-metric]');
      if (!button) return;
      const metric = button.dataset.calendarMetric;
      document.querySelectorAll('[data-calendar-view]').forEach((view) => {
        view.hidden = view.dataset.calendarView !== metric;
      });
      calendarControls.querySelectorAll('button[data-calendar-metric]').forEach((candidate) => {
        candidate.setAttribute('aria-pressed', String(candidate === button));
      });
    });
  });
})();
"""
