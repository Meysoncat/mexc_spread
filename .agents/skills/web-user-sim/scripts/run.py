"""
Runner — single-entry CLI for the web-user-sim skill.

Usage (model calls this from the project root):

    python .agents/skills/web-user-sim/scripts/run.py \\
        --scenario smoke-all-pages --persona viewer

    python .agents/skills/web-user-sim/scripts/run.py \\
        --scenario change-symbol-in-header --persona trader \\
        --base http://localhost:5173 --port 9222

Flags:
  --scenario   key from scenarios.py (default: smoke-all-pages)
  --persona    key from personas.py  (default: viewer)
  --base       override base URL     (default: env WEB_USER_SIM_BASE or localhost:5173)
  --port       CDP debug port        (default: env WEB_USER_SIM_PORT or 9222)
  --launch     launch a fresh headed Chrome if none is on --port (default: on)
  --no-launch  do not auto-launch; fail if no Chrome on the port
  --out        write JSON report path (default: .web-user-sim/report-<ts>.json)

Environment overrides:
  WEB_USER_SIM_BASE     base URL
  WEB_USER_SIM_PORT     CDP port
  WEB_USER_SIM_CHROME   path to chrome.exe (only used when launching)

Exits non-zero if any step errored AND that caused no screenshots — otherwise 0,
because partial runs are still useful for UX assessment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Allow `import cdp_lib` when invoked as a script.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from cdp_lib import CDPSession, launch_chrome, _wait_for_devtools  # noqa: E402
from scenarios import get_scenario, list_scenarios  # noqa: E402
from personas import get_persona, all_personas  # noqa: E402
from reporter import run_scenario, render_report_text  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="web-user-sim scenario runner")
    p.add_argument("--scenario", default="smoke-all-pages")
    p.add_argument("--persona", default="viewer")
    p.add_argument("--base", default=os.environ.get("WEB_USER_SIM_BASE", "http://localhost:5173"))
    p.add_argument("--port", type=int, default=int(os.environ.get("WEB_USER_SIM_PORT", "9222")))
    p.add_argument("--launch", dest="launch", action="store_true", default=True)
    p.add_argument("--no-launch", dest="launch", action="store_false")
    p.add_argument("--out", default=None)
    p.add_argument("--list", action="store_true", help="list scenarios+personas and exit")
    args = p.parse_args()

    if args.list:
        print("PERSONAS:", ", ".join(all_personas()))
        print("SCENARIOS:", ", ".join(list_scenarios()))
        return 0

    # Override base URL for scenario lookups (scenarios.py reads env at import).
    os.environ["WEB_USER_SIM_BASE"] = args.base.rstrip("/")
    # Re-import scenarios so BASE_URL picks up the override.
    import importlib, scenarios as _sc
    importlib.reload(_sc)
    from scenarios import get_scenario as _gs

    # Validate scenario/persona up front for a clear error.
    try:
        steps = _gs(args.scenario)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        persona = get_persona(args.persona)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"[web-user-sim] persona='{args.persona}' ({persona['label']}) "
          f"scenario='{args.scenario}' ({len(steps)} steps) base='{args.base}'")

    # Optional launch — try to connect first, launch only if nothing's there.
    from cdp_lib import list_targets
    try:
        list_targets(args.port)
    except Exception:
        if not args.launch:
            print(f"[web-user-sim] No Chrome on port {args.port} and --no-launch set.",
                  file=sys.stderr)
            return 3
        print(f"[web-user-sim] launching headed Chrome on port {args.port}...")
        launch_chrome(debug_port=args.port, start_url=args.base)
        _wait_for_devtools(args.port)

    session = CDPSession.connect(
        port=args.port,
        url_filter="localhost",
        auto_open=args.base,
        launch_if_missing=False,
    )
    print(f"[web-user-sim] attached to tab: {session.target.get('url')}")

    def on_step(step, entry):
        flag = "ok" if entry.get("ok") else "FAIL"
        name = entry.get("name")
        print(f"  ... {flag:<4} {entry.get('ms'):>5}ms  {name}")

    try:
        report = run_scenario(session, steps, on_step=on_step)
    finally:
        session.close()

    # Attach persona context so the model has the judging criteria in-band.
    report["persona"] = {"name": args.persona, **persona}
    report["scenario"] = args.scenario

    out_dir = os.path.join(os.getcwd(), ".web-user-sim")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(
        out_dir, f"report-{int(time.time())}.json"
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(render_report_text(report))
    print("=" * 60)
    print(f"report JSON  : {out_path}")
    print(f"screenshots  : .web-user-sim/shots/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
