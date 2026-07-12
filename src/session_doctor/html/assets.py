# ruff: noqa: E501

STYLES = r"""
:root {
  color-scheme: light dark;
  --bg: #f5f7fa;
  --surface: #ffffff;
  --surface-alt: #eef2f6;
  --text: #17202a;
  --muted: #556474;
  --border: #c8d1dc;
  --accent: #2457a6;
  --accent-soft: #dce8fa;
  --risk: #a33a1f;
  --risk-soft: #f9dfd6;
  --positive: #287044;
  --positive-soft: #dcefe4;
  --unavailable: #6b6475;
  --unavailable-soft: #e9e5ed;
  --focus: #1267d6;
  --shadow: 0 1px 3px rgb(20 35 55 / 12%);
  --radius: .65rem;
  --space-1: .25rem;
  --space-2: .5rem;
  --space-3: .75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #11161d;
    --surface: #19212b;
    --surface-alt: #222c38;
    --text: #eef3f8;
    --muted: #b3bfcc;
    --border: #455363;
    --accent: #86b7ff;
    --accent-soft: #263f60;
    --risk: #ff9d82;
    --risk-soft: #572d27;
    --positive: #8bd2a5;
    --positive-soft: #234b33;
    --unavailable: #c2b8cc;
    --unavailable-soft: #413a49;
    --focus: #91c3ff;
    --shadow: none;
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--text); line-height: 1.55; }
a { color: var(--accent); }
a:focus-visible, button:focus-visible, summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.skip-link { position: absolute; left: var(--space-4); top: -5rem; z-index: 10; padding: var(--space-3); background: var(--surface); border: 2px solid var(--focus); }
.skip-link:focus { top: var(--space-4); }
.page { width: min(76rem, calc(100% - 2rem)); margin: 0 auto; padding: var(--space-6) 0 4rem; }
header[role="banner"] { margin-bottom: var(--space-6); }
h1, h2, h3 { line-height: 1.2; letter-spacing: -.015em; }
h1 { margin: 0 0 var(--space-2); font-size: clamp(1.8rem, 4vw, 3rem); overflow-wrap: anywhere; }
.session-key { margin: 0 0 var(--space-3); color: var(--muted); overflow-wrap: anywhere; }
.session-key code { font-size: clamp(.8rem, 2vw, 1.05rem); }
h2 { margin: 0 0 var(--space-4); font-size: clamp(1.35rem, 2.5vw, 1.8rem); }
h3 { margin-top: 0; font-size: 1.1rem; }
.lede, .muted { color: var(--muted); }
.section { margin-block: var(--space-6); scroll-margin-top: var(--space-4); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 17rem), 1fr)); gap: var(--space-4); }
.card { min-width: 0; padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow); }
.card > :last-child { margin-bottom: 0; }
.status-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
.badge { display: inline-flex; align-items: center; border: 1px solid currentColor; border-radius: 999px; padding: .12rem .55rem; font-size: .8rem; font-weight: 700; letter-spacing: .02em; }
.badge.current, .badge.available { color: var(--positive); background: var(--positive-soft); }
.badge.neutral { color: var(--accent); background: var(--accent-soft); }
.badge.risk { color: var(--risk); background: var(--risk-soft); }
.badge.stale, .badge.missing, .badge.unavailable { color: var(--unavailable); background: var(--unavailable-soft); }
dl.meta { display: grid; grid-template-columns: minmax(8rem, auto) minmax(0, 1fr); gap: var(--space-2) var(--space-4); margin: 0; }
dl.meta dt { color: var(--muted); font-weight: 600; }
dl.meta dd { margin: 0; overflow-wrap: anywhere; }
.stat { font-size: 1.65rem; font-weight: 750; line-height: 1.1; }
.stat-label { color: var(--muted); font-size: .9rem; }
.score-row { display: grid; grid-template-columns: minmax(10rem, 1fr) minmax(12rem, 3fr) 4.5rem; align-items: center; gap: var(--space-3); margin-block: var(--space-3); }
progress { width: 100%; height: .8rem; border: 0; border-radius: 999px; overflow: hidden; background: var(--surface-alt); }
progress::-webkit-progress-bar { background: var(--surface-alt); }
progress::-webkit-progress-value { background: var(--accent); }
progress::-moz-progress-bar { background: var(--accent); }
.score-value { text-align: right; font-variant-numeric: tabular-nums; }
.chart-scroll { overflow-x: auto; padding-bottom: var(--space-2); }
.sequence-chart { display: block; min-width: 48rem; width: 100%; height: auto; color: var(--text); }
.sequence-chart .gridline { stroke: var(--border); stroke-width: 1; }
.sequence-chart .lane-label { fill: var(--muted); font-size: 12px; }
.sequence-chart .activity { fill: var(--accent); }
.sequence-chart .activity-risk { fill: var(--risk); }
.sequence-chart .marker { stroke: var(--risk); stroke-width: 1.5; fill: var(--surface); }
.legend { display: flex; flex-wrap: wrap; gap: var(--space-3); padding: 0; list-style: none; color: var(--muted); font-size: .88rem; }
.legend-key { display: inline-block; width: .8rem; height: .8rem; margin-right: var(--space-1); background: var(--accent); border-radius: 2px; }
.legend-key.risk { background: var(--risk); }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .92rem; }
th, td { padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { color: var(--muted); font-weight: 650; }
code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9em; overflow-wrap: anywhere; }
details { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); margin-block: var(--space-3); }
summary { cursor: pointer; padding: var(--space-3) var(--space-4); font-weight: 700; }
details > .details-body { padding: 0 var(--space-4) var(--space-4); }
.empty { padding: var(--space-4); border: 1px dashed var(--border); border-radius: var(--radius); color: var(--muted); background: var(--surface-alt); }
.notice { border-left: .3rem solid var(--unavailable); padding: var(--space-3) var(--space-4); background: var(--unavailable-soft); }
.notice.risk { border-color: var(--risk); background: var(--risk-soft); }
.controls { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-3); }
.controls[hidden] { display: none; }
button { border: 1px solid var(--border); border-radius: .4rem; padding: .45rem .75rem; color: var(--text); background: var(--surface); font: inherit; cursor: pointer; }
ul.clean { padding-left: 1.2rem; }
@media (max-width: 42rem) {
  .page { width: min(100% - 1rem, 76rem); padding-top: var(--space-4); }
  .card { padding: var(--space-3); }
  dl.meta { grid-template-columns: minmax(7rem, 42%) minmax(0, 1fr); gap: var(--space-2); }
  .score-row { grid-template-columns: 1fr auto; }
  .score-row progress { grid-column: 1 / -1; grid-row: 2; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
@media print {
  :root { color-scheme: light; --bg: #fff; --surface: #fff; --surface-alt: #f3f3f3; --text: #000; --muted: #333; --border: #888; --accent: #174d91; --risk: #8a2413; --positive: #1d6337; --unavailable: #555; --shadow: none; }
  @page { margin: 1.2cm; }
  body { background: #fff; font-size: 10pt; }
  .page { width: 100%; padding: 0; }
  .skip-link, .controls, script { display: none !important; }
  .section, .card, details { break-inside: avoid; }
  details:not([open]) > .details-body { display: block !important; }
  .chart-scroll { overflow: visible; }
  .sequence-chart { min-width: 0; }
  a { color: inherit; text-decoration: none; }
}
"""

SCRIPT = r"""
(() => {
  document.documentElement.classList.add('js');
  const controls = document.querySelector('[data-disclosure-controls]');
  if (!controls) return;
  controls.hidden = false;
  controls.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-disclosure-action]');
    if (!button) return;
    const open = button.dataset.disclosureAction === 'open';
    document.querySelectorAll('main details').forEach((detail) => { detail.open = open; });
  });
})();
"""
