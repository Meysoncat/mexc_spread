# web-user-sim

Skill that lets the agent act like a real user of a web app: open Chrome, walk
pages, click, type, scroll, screenshot, and collect what the browser reports.
Then judge the UX from a chosen persona's point of view.

See `SKILL.md` for the full workflow. This file is a short reference.

## Quick start

```bash
# from the project root
python .agents/skills/web-user-sim/scripts/run.py --list      # see options
python .agents/skills/web-user-sim/scripts/run.py \
    --scenario smoke-all-pages --persona viewer
```

Outputs:

- `.web-user-sim/report-<ts>.json` — full structured report
- `.web-user-sim/shots/*.png` — screenshots per step
- A rendered text summary on stdout

## Requirements

- Chrome installed (Windows path auto-detected; override with `WEB_USER_SIM_CHROME`)
- `websocket-client` (already in this repo's venv)
- The web app running (frontend on 5173, API on 8006 by default)

## Common flags

| Flag | Default | Purpose |
|---|---|---|
| `--scenario` | `smoke-all-pages` | Which user path to run |
| `--persona` | `viewer` | Which judging criteria to apply |
| `--base` | `http://localhost:5173` | Target URL (or `WEB_USER_SIM_BASE`) |
| `--port` | `9222` | Chrome remote-debugging port (or `WEB_USER_SIM_PORT`) |
| `--no-launch` | off | Don't auto-launch Chrome; fail if none on the port |
| `--out` | auto | Where to write the JSON report |

## Troubleshooting

- **"Chrome not found"** — set `WEB_USER_SIM_CHROME` to your `chrome.exe`.
- **Every `/api/*` is 401** — the API is up but auth didn't bootstrap; check
  `/api/admin-token` is reachable from the browser, not whether the page is broken.
- **A click step fails** — selector may have changed; check `steps[].error` in
  the report, then update the selector in `scripts/scenarios.py`.
- **"0 rows" but the page looks full in the screenshot** — the table is
  virtualized; `tbody tr` undercounts. Trust the screenshot.
- **Runner hangs** — Chrome didn't expose `/json` in time. Kill any stale
  Chrome on port 9222 and retry; the runner launches a fresh headed instance.

## Using the library directly

```python
import sys; sys.path.insert(0, ".agents/skills/web-user-sim/scripts")
from cdp_lib import CDPSession

s = CDPSession.connect(port=9222, auto_open="http://localhost:5173/")
s.goto("http://localhost:5173/arbitrage", wait_until="idle")
s.click("button.start")
print(s.dom_summary())
print(s.api_failures())
s.screenshot("after-click.png")
s.close()
```

This is the escape hatch when no predefined scenario fits.
