---
name: web-user-sim
description: >-
  Drive a real browser (Chrome via the DevTools Protocol) to act like a human user
  and assess a web application from the user's perspective. Use whenever the user
  asks to "протестировать/оценить веб-приложение как пользователь", "пройти по
  страницам", "проверить UX", "имитировать действия пользователя", "пощёлкать по
  кнопкам", "проверить, как себя ведёт фронтенд", "сделать smoke-test UI",
  "прогнать сценарий пользователя", or wants screenshots/observations of how the
  app behaves end-to-end in a browser. Also use when debugging frontend issues
  that only reproduce at runtime in the browser (DOM, React state, console
  errors, failed /api calls). This is a browser-automation + UX-assessment
  skill, not a static code review.
---

# web-user-sim

Act like a real user: open the running web app in Chrome, walk through pages,
click buttons, type into inputs, scroll, take screenshots, and collect what the
browser reports (console errors, JS exceptions, failed `/api/*` requests, DOM
structure). Then judge the experience from a chosen persona's point of view and
report findings to the user.

## When to use

Trigger this skill for requests like:

- "Оцени веб-приложение как пользователь" / "протестируй UX"
- "Пройдись по всем страницам и скажи что сломано"
- "Проверь, как ведёт себя фронтенд при кликах"
- "Smoke-test UI" / "прогони пользовательские сценарии"
- "Сделай скриншоты каждой страницы"
- Runtime frontend bugs: "почему на /arbitrage пусто", "что в консоли при клике"

Do NOT trigger for pure static code analysis — that needs no browser.

## What it assumes

- The target web app is reachable at a URL (default `http://localhost:5173`).
  For this repo (mexc_spread_monitor) that means the modern UI is running —
  `run_modern.bat` or `npm run dev:modern` (frontend on 5173, API on 8006).
- Chrome is installed. On Windows the default path is auto-detected; override
  with the `WEB_USER_SIM_CHROME` env var.
- `websocket-client` is installed (it is, in this repo's venv and system Python).

## Workflow

### 1. Make sure the app is running

If you are unsure, check both ports first:

```bash
curl -s http://127.0.0.1:8006/api/health || echo "API down"
curl -s http://localhost:5173/ -o /dev/null -w "frontend HTTP %{http_code}\n" || echo "frontend down"
```

If either is down, tell the user and offer to start `run_modern.bat` (it's
long-running — run in background). Do not proceed with the simulation against a
dead server; every page will look broken and the report will be misleading.

### 2. Run a scenario

Everything is driven by one CLI. From the project root:

```bash
python .agents/skills/web-user-sim/scripts/run.py \
    --scenario smoke-all-pages --persona viewer
```

The runner will:

1. Connect to Chrome on port 9222 (or launch a headed instance if none is there).
2. Walk the scenario steps (goto / click / type / scroll / screenshot / wait_for).
3. Print a live step-by-step log.
4. Write a JSON report to `.web-user-sim/report-<ts>.json`.
5. Save screenshots to `.web-user-sim/shots/`.

List available scenarios and personas:

```bash
python .agents/skills/web-user-sim/scripts/run.py --list
```

**Scenarios** (defined in `scripts/scenarios.py`):

- `smoke-all-pages` — visit all 11 routes, screenshot each, flag broken ones.
- `first-time-visitor` — land on `/`, look around, judge clarity.
- `change-symbol-in-header` — pick a symbol via the `GlobalAssetBar` input
  (the React-aware setter that updates app state).
- `alerts-form-interaction` — open `/alerts`, hover buttons, judge form usability.
- `trader-flow` — `/trading` → `/arbitrage` → `/futures-arb` as a trader.

**Personas** (defined in `scripts/personas.py`) — each changes *how to judge*
the same report:

- `first-time` — clarity, empty states, scary errors
- `viewer` — load speed, table readability, symbol switching
- `trader` — control responsiveness, positions/PnL, kill-switch visibility
- `alerts` — form usability
- `dev` — console errors, JS exceptions, `/api/*` ≥400, React DOM warnings

### 3. Read the report

After the run, read the JSON report and the rendered text summary the runner
already printed. The key fields:

- `totals` — `duration_ms`, `errors`, `exceptions`, `api_failures`
- `api_failures` — deduped list of `{url, status}` for `/api/*` ≥400
- `exceptions` — JS exceptions thrown
- `console_errors` — `console.error` calls
- `final_dom` — last page's structural snapshot (url, title, counts, error banners)
- `steps[]` — per-step timing + screenshot path + error (if any)
- `screenshots[]` — paths to all PNGs
- `persona` — the judging criteria for this run

Use the **Read** tool to view any screenshot you want to reason about visually.
The model is a vision-capable agent — looking at a screenshot often reveals
things the DOM summary hides (layout breakage, invisible-but-present errors,
visual polish issues).

### 4. Assess against the persona

This is the part that adds value beyond "did it error". Walk the persona's
`cares_about` list and answer each one using the report + screenshots:

> Persona `viewer` cares: "Скорость появления данных на главной (TTI)"
> → From the report, the `goto /` step took 3200ms and `tableRows` ended at 34.
>   Acceptable but not snappy; below the fold was still loading after screenshot.

Write the assessment in the user's language (Russian by default for this
project) and be specific — quote timings, status codes, error text. Don't
generalize ("looks good") without evidence from the run.

### 5. Report back

Structure the final answer:

1. **Что проверено** — scenario + persona + base URL.
2. **Сводка** — pass/fail counts, total time, screenshot count.
3. **Найденные проблемы** — sorted by severity (exceptions > api failures >
   console errors > UX issues from screenshots).
4. **Что хорошо** — at least one thing that worked, so it's not all complaints.
5. **Скриншоты** — list of paths so the user can open them.

## Important caveats — read before reporting

- **Auth**: pages that call protected endpoints return 401 without an admin
  token. In this app, the frontend fetches `/api/admin-token` on startup and
  stores it in localStorage; if you launched a *fresh* Chrome profile (the
  runner does), that bootstrap must succeed. If you see 401s everywhere, first
  check whether the API is even reachable, not whether the page is broken.
- **Virtualized tables**: row counts from `tbody tr` undercount virtualized
  lists. A "0 rows" result doesn't always mean empty — check the screenshot.
- **Timing is timing**: a slow step might be backend latency, not a frontend
  bug. Cross-reference with the `/api/*` failure list before blaming the UI.
- **Selectors are best-effort**: scenarios use semantic CSS (`header input`,
  `tbody tr`, `button`). If the app's class names or layout change, a step may
  fail to find an element — that is a scenario-maintenance issue, not
  necessarily an app bug. Read the step's `error` field.

## Customizing

- **Add a scenario**: edit `scripts/scenarios.py` and append to `SCENARIOS`.
  Each step is a small dict (see the docstring at the top of that file).
- **Add a persona**: edit `scripts/personas.py`.
- **Point at a different URL**: `--base http://my-host:1234/` or set
  `WEB_USER_SIM_BASE`.
- **Use an already-running Chrome** (not the auto-launched one): start Chrome
  yourself with `--remote-debugging-port=9222 --user-data-dir=...`, then run
  with `--no-launch`.
- **Use the library directly** instead of the CLI: `import` `cdp_lib.CDPSession`
  in a one-off script — useful when a scenario doesn't fit and you want
  fine-grained control (e.g. `s.click(...)`, `s.eval(...)`, `s.screenshot()`).

## File layout

```
web-user-sim/
├── SKILL.md              ← this file
├── README.md             ← short reference + troubleshooting
└── scripts/
    ├── cdp_lib.py        ← CDPSession: connect/click/type/wait/screenshot/eval
    ├── personas.py       ← user personas and their judging criteria
    ├── scenarios.py      ← concrete user paths through the app
    ├── reporter.py       ← runs steps + aggregates events into a report
    └── run.py            ← CLI entry point the model invokes
```

Artifacts produced at runtime go to `.web-user-sim/` in the project root (not
inside the skill) so they can be gitignored and don't pollute the skill itself.
