"""
Reporter — runs a scenario step-by-step through a CDPSession and produces a UX
report dict suitable for the model to read and judge.

The output of run_scenario() is the single source of truth the model consumes:
  - per-step timing + screenshot paths
  - aggregate console errors / exceptions / api failures
  - final dom_summary()

The model's job afterwards is to interpret this report against the persona's
"cares_about" criteria and write a human-readable UX assessment.
"""

from __future__ import annotations

import json
import time
from typing import Any

from cdp_lib import CDPSession


def _step_name(step: dict, default: str) -> str:
    return step.get("name") or default


def run_scenario(
    session: CDPSession,
    steps: list[dict],
    on_step: Any = None,
) -> dict:
    """
    Execute `steps` on an already-connected `session`.

    `on_step(step, result)` optional callback fired after each step (for live logs).

    Returns a report dict:
      {
        "steps": [ {name, kind, ok, ms, screenshot?, note?, error?} ],
        "screenshots": [path, ...],
        "totals": {duration_ms, errors, exceptions, api_failures},
        "final_dom": {...},
        "all_events": [ ... ]
      }
    """
    report: dict[str, Any] = {
        "steps": [],
        "screenshots": [],
        "totals": {},
        "final_dom": {},
        "all_events": [],
    }
    started = time.time()
    api_failures = session.api_failures()  # baseline (likely empty after fresh goto)

    for idx, step in enumerate(steps):
        kind = next(iter(step.keys())) if step else "noop"
        # 'expect' and 'note' are not browser actions; record them and continue.
        if kind in ("expect", "note"):
            entry = {
                "name": _step_name(step, kind),
                "kind": kind,
                "ok": True,
                "ms": 0,
                kind: step[kind],
            }
            report["steps"].append(entry)
            if on_step:
                on_step(step, entry)
            continue

        t0 = time.time()
        entry: dict[str, Any] = {
            "name": _step_name(step, f"step-{idx}"),
            "kind": kind,
            "ok": False,
            "ms": 0,
        }
        try:
            if kind == "goto":
                session.goto(step["goto"], wait_until="idle", timeout=10)
                entry["ok"] = True
            elif kind == "click":
                entry["ok"] = bool(session.click(step["click"]))
            elif kind == "hover":
                entry["ok"] = bool(session.hover(step["hover"]))
            elif kind == "type":
                selector, text = step["type"]
                entry["ok"] = bool(session.type_text(selector, text))
            elif kind == "scroll":
                session.scroll(y=int(step["scroll"]))
                entry["ok"] = True
            elif kind == "screenshot":
                # Save into a per-run shots dir with the requested name.
                import os
                shots_dir = os.path.join(os.getcwd(), ".web-user-sim", "shots")
                os.makedirs(shots_dir, exist_ok=True)
                path = os.path.join(shots_dir, step["screenshot"])
                session.screenshot(path=path)
                entry["screenshot"] = path
                report["screenshots"].append(path)
                entry["ok"] = True
            elif kind == "wait_for":
                # Allow a literal "true" to mean "settle a moment".
                pred = step["wait_for"]
                if pred.strip() == "true":
                    session.drain(0.6)
                    entry["ok"] = True
                else:
                    entry["ok"] = bool(session.wait_for(pred, timeout=12))
            else:
                entry["error"] = f"unknown step kind: {kind}"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:300]

        entry["ms"] = round((time.time() - t0) * 1000)
        report["steps"].append(entry)
        if on_step:
            on_step(step, entry)

    duration_ms = round((time.time() - started) * 1000)

    # Aggregate events captured across the whole run.
    events = session.collect_events(clear=False)
    errors = [e for e in events if e.get("kind") == "console" and e.get("level") == "error"]
    exceptions = [e for e in events if e.get("kind") == "exception"]
    # API failures: collect from this run's net_response events with status>=400
    api_fail = [
        e for e in events
        if e.get("kind") == "net_response"
        and "/api/" in e.get("url", "")
        and e.get("status", 0) >= 400
    ]
    # Dedup api failures by url+status (a 401 can fire many times per page).
    seen = set()
    api_fail_dedup = []
    for e in api_fail:
        key = (e["url"], e["status"])
        if key in seen:
            continue
        seen.add(key)
        api_fail_dedup.append(e)

    report["totals"] = {
        "duration_ms": duration_ms,
        "errors": len(errors),
        "exceptions": len(exceptions),
        "api_failures": len(api_fail_dedup),
    }
    report["final_dom"] = session.dom_summary()
    report["all_events"] = events
    report["api_failures"] = api_fail_dedup
    report["console_errors"] = errors
    report["exceptions"] = exceptions
    return report


def render_report_text(report: dict) -> str:
    """Human-readable summary of the run — feed this back to the model/user."""
    lines = []
    totals = report.get("totals", {})
    lines.append(
        f"RUN: {totals.get('duration_ms')}ms | "
        f"errors={totals.get('errors')} exceptions={totals.get('exceptions')} "
        f"api_failures={totals.get('api_failures')}"
    )
    dom = report.get("final_dom", {})
    lines.append(
        f"FINAL: url={dom.get('url')} title={dom.get('title')!r} "
        f"buttons={dom.get('buttons')} inputs={dom.get('inputs')} "
        f"tables={dom.get('tables')} rows={dom.get('tableRows')}"
    )
    if dom.get("errorBanners"):
        lines.append("ERROR BANNERS: " + " | ".join(dom["errorBanners"]))
    for e in report.get("api_failures", []):
        lines.append(f"  API FAIL {e['status']} {e['url']}")
    for e in report.get("exceptions", [])[:5]:
        lines.append(f"  EXCEPTION {e.get('text','')[:160]}")
    for e in report.get("console_errors", [])[:5]:
        lines.append(f"  CONSOLE.ERR {e.get('text','')[:160]}")
    lines.append("STEPS:")
    for s in report.get("steps", []):
        flag = "OK " if s.get("ok") else "FAIL"
        extra = ""
        if s.get("screenshot"):
            extra = f" -> {s['screenshot']}"
        elif s.get("error"):
            extra = f" !! {s['error']}"
        lines.append(f"  [{flag}] {s.get('ms'):>5}ms  {s.get('kind'):<10} {s.get('name')}{extra}")
    return "\n".join(lines)
