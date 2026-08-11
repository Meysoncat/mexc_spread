"""
CDPSession — thin reusable wrapper over the Chrome DevTools Protocol (raw WebSocket).

Designed for the web-user-sim skill: lets the model act like a real user — navigate,
click, type, scroll, take screenshots, evaluate JS, and collect page events
(console, exceptions, network failures) without any heavy framework
(no Playwright/Selenium — only `websocket-client`, already installed).

Connects to a Chrome instance launched with --remote-debugging-port (default 9222).
See SKILL.md "Launch Chrome" for how to start that browser.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import websocket  # websocket-client
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "websocket-client is required: pip install websocket-client"
    ) from exc


CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


@dataclass
class PageEvent:
    """One captured browser event (console msg, exception, network failure)."""

    kind: str            # 'console' | 'exception' | 'net_failed' | 'net_response'
    level: str = ""      # for console: 'log'|'warning'|'error'
    text: str = ""
    url: str = ""
    status: int = 0      # for net_response
    when: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "level": self.level, "text": self.text,
            "url": self.url, "status": self.status, "when": round(self.when, 2),
        }


def _find_chrome() -> Optional[str]:
    env = os.environ.get("WEB_USER_SIM_CHROME")
    if env and os.path.exists(env):
        return env
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def launch_chrome(
    debug_port: int = 9222,
    user_data_dir: Optional[str] = None,
    start_url: str = "about:blank",
    extra_args: Optional[list[str]] = None,
) -> subprocess.Popen:
    """
    Launch Chrome with remote debugging enabled and return the Popen handle.

    Uses a dedicated --user-data-dir so it never collides with the user's real
    Chrome profile (avoids "Chrome is being controlled by automated software"
    conflicts and profile lock issues).

    Set WEB_USER_SIM_CHROME env var to override the Chrome binary path.
    """
    chrome = _find_chrome()
    if not chrome:
        raise SystemExit(
            "Chrome not found. Set WEB_USER_SIM_CHROME env var to chrome.exe path."
        )
    if user_data_dir is None:
        user_data_dir = os.path.join(
            os.environ.get("TEMP", "/tmp"), f"web-user-sim-profile-{debug_port}"
        )
    args = [
        chrome,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        # Modern Chrome rejects WS connections to the CDP endpoint unless the
        # origin is explicitly allowlisted. Without this flag, attaching via
        # websocket-client fails with "Handshake status 403 Forbidden".
        "--remote-allow-origins=*",
        start_url,
    ]
    if extra_args:
        args.extend(extra_args)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_for_devtools(port: int, timeout: float = 20.0) -> None:
    """Block until Chrome's /json endpoint responds."""
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ).read()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise TimeoutError(
        f"Chrome on port {port} did not expose /json within {timeout}s: {last_err}"
    )


def list_targets(port: int = 9222) -> list[dict]:
    """Return Chrome's list of open targets (tabs/pages)."""
    raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read()
    return json.loads(raw)


def create_new_tab(port: int = 9222, url: str = "about:blank") -> dict:
    """
    Open a fresh tab and return its target descriptor.

    Newer Chrome requires PUT (not GET) on /json/new — older versions accepted
    GET, so we try PUT first and fall back to GET for compatibility.
    """
    import urllib.request as urlreq

    endpoint = f"http://127.0.0.1:{port}/json/new?{url}"
    # Try PUT first (modern Chrome), then GET (older Chrome / Chromium).
    for method in ("PUT", "GET"):
        try:
            req = urlreq.Request(endpoint, method=method)
            raw = urlreq.urlopen(req, timeout=5).read()
            return json.loads(raw)
        except urlreq.HTTPError as exc:
            if exc.code in (405, 400):
                continue  # try the other method
            raise
    # If both failed, surface a clear error.
    raise RuntimeError(f"Could not create new tab on port {port} via PUT or GET")


class CDPSession:
    """
    Active CDP connection to a single page (tab).

    Lifecycle:
        s = CDPSession.connect(port=9222)   # finds/opens a tab and attaches
        s.goto("http://localhost:5173/")
        s.click("button.primary")
        s.type("input[name=symbol]", "SOLUSDT\n")
        png = s.screenshot()
        report = s.collect_events()
        s.close()
    """

    def __init__(self, ws, target: dict):
        self.ws = ws
        self.target = target
        self._id = 0
        self._events: list[PageEvent] = []
        self._nav_id: Optional[str] = None  # currently-tracked navigation loaderId
        self._frame_stopped = False
        self._enabled_domains = False
        # Counter of *new* outgoing requests since session start. Used to detect
        # network idle for SPA navigations: long-lived streams
        # (/api/*stream, EventSource, websocket) keep producing responseReceived
        # events forever, so "responses went quiet" is the wrong signal. Counting
        # requestWillBeSent instead lets us detect "the page stopped asking for
        # anything new", which is what actually means "SPA finished loading".
        self._req_sent_count = 0
        # Counter for *all* network responses received (regardless of whether we
        # store them). Cheaper than scanning the event list for idle detection.
        self._net_resp_count = 0
        # Cap on captured events. Long sessions with chatty streams (snapshot
        # SSE, ws feeds) can otherwise grow this list unbounded and waste memory.
        self._max_events = 2000

    # ---------- connection ----------

    @classmethod
    def connect(
        cls,
        port: int = 9222,
        url_filter: str = "localhost",
        auto_open: str = "http://localhost:5173/",
        launch_if_missing: bool = True,
    ) -> "CDPSession":
        """
        Attach to an existing tab whose URL contains `url_filter`.
        If none exists and launch_if_missing is True, opens a new tab at `auto_open`.
        """
        try:
            targets = list_targets(port)
        except Exception:  # noqa: BLE001
            targets = []
            if launch_if_missing:
                launch_chrome(debug_port=port)
                _wait_for_devtools(port)

        target = next(
            (t for t in targets
             if t.get("type") == "page" and url_filter in t.get("url", "")),
            None,
        )
        if target is None:
            if not launch_if_missing and not targets:
                raise RuntimeError(
                    f"No Chrome with --remote-debugging-port={port} found. "
                    "Start one (see SKILL.md) or pass launch_if_missing=True."
                )
            target = create_new_tab(port, url=auto_open)
            time.sleep(0.6)

        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20)
        s = cls(ws, target)
        s._enable_domains()
        return s

    def _enable_domains(self) -> None:
        """Turn on the CDP domains we listen to for events."""
        if self._enabled_domains:
            return
        for method in (
            "Page.enable",
            "Runtime.enable",
            "Network.enable",
            "Log.enable",
            "DOM.enable",
        ):
            self._send_raw(method, {})
        # Disable cache so each navigation reflects fresh backend state.
        try:
            self._send_raw("Network.setCacheDisabled", {"cacheDisabled": True})
        except Exception:  # noqa: BLE001
            pass
        self._enabled_domains = True
        self.drain()

    # ---------- low-level CDP plumbing ----------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send_raw(self, method: str, params: Optional[dict]) -> dict:
        """
        Send a CDP request and read exactly one matched response, draining events.

        Uses a short per-recv timeout and retries, so a burst of events during
        navigation (e.g. when /api/*stream subscriptions fire) can't deadlock the
        socket. We also cap the event buffer to avoid unbounded memory growth on
        long sessions with chatty streams.
        """
        req_id = self._next_id()
        self.ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        deadline = time.time() + 30.0  # hard cap; CDP should always reply well before this
        # Short per-recv timeout so we never block forever on one recv().
        self.ws.settimeout(0.5)
        try:
            while time.time() < deadline:
                try:
                    raw = self.ws.recv()
                except Exception as exc:  # timeout or transient read error
                    if "timed out" in str(exc).lower():
                        continue  # harmless: try again until deadline
                    raise  # anything else is a real socket error
                if not raw:
                    continue
                msg = json.loads(raw)
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise RuntimeError(f"CDP error on {method}: {msg['error']}")
                    return msg.get("result", {})
                self._dispatch_event(msg)
            raise TimeoutError(f"CDP no reply to {method} within 30s")
        finally:
            self.ws.settimeout(20)

    def _fire_and_forget(self, method: str, params: Optional[dict]) -> None:
        """
        Send a CDP request but don't wait for its response — just send and drain
        any events that arrive. Used for Page.navigate, because on some SPA
        transitions Chrome never returns a response to the navigate call (the
        page redirects, or the loader stays open) and blocking on the reply
        would deadlock us. We rely on Page.frameStoppedLoading + idle polling
        instead to know when the navigation settled.
        """
        req_id = self._next_id()
        self.ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        # Give Chrome a moment to ingest the command and emit early events,
        # but never block on its response.
        self.drain(0.1)

    def _dispatch_event(self, msg: dict) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        if method == "Runtime.consoleAPICalled":
            level = params.get("type", "log")
            args = params.get("args", [])
            text = " ".join(
                str(a.get("value", a.get("description", ""))) for a in args
            ).strip()
            self._events.append(PageEvent("console", level=level, text=text))
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            self._events.append(
                PageEvent(
                    "exception",
                    text=details.get("text", ""),
                    url=details.get("url", ""),
                )
            )
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            self._events.append(
                PageEvent(
                    "console",
                    level=entry.get("level", "log"),
                    text=entry.get("text", ""),
                    url=entry.get("url", ""),
                )
            )
        elif method == "Network.responseReceived":
            resp = params.get("response", {})
            url = resp.get("url", "")
            status = resp.get("status", 0)
            # Only keep API responses (the ones we actually judge) + errors.
            # Random static-asset 200s just bloat the buffer.
            if "/api/" in url or status >= 400:
                self._events.append(
                    PageEvent("net_response", url=url, status=status, text=str(status))
                )
            # Always bump a separate counter for idle detection (cheap).
            self._net_resp_count += 1
        elif method == "Network.loadingFailed":
            self._events.append(
                PageEvent(
                    "net_failed",
                    url=params.get("requestId", ""),
                    text=params.get("errorText", "FAILED"),
                )
            )
        elif method == "Network.requestWillBeSent":
            # Count new outgoing requests. We intentionally count *every* one
            # (including streams) — what matters for idle detection is whether
            # the count keeps changing, not the absolute number.
            self._req_sent_count += 1
        elif method == "Page.frameStoppedLoading":
            self._frame_stopped = True

        # Bound the event buffer: if it grows too large, drop the oldest. This
        # matters on long runs against this app because snapshot/stream and ws
        # feeds fire continuously.
        if len(self._events) > self._max_events:
            del self._events[: len(self._events) - self._max_events]

    def drain(self, settle: float = 0.3) -> None:
        """Flush any buffered events without blocking."""
        self.ws.settimeout(settle)
        try:
            while True:
                raw = self.ws.recv()
                if raw:
                    self._dispatch_event(json.loads(raw))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.ws.settimeout(20)

    # ---------- high-level primitives ----------

    def eval(self, expression: str, await_promise: bool = False) -> Any:
        """
        Evaluate JS in the page and return the JS value (JSON-serializable).
        For object/array results, JSON.stringify in the expression yourself.
        """
        result = self._send_raw(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        res = result.get("result", {})
        if res.get("subtype") == "error" or res.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {res.get('exceptionDetails') or res}")
        return res.get("value")

    def goto(
        self, url: str, wait_until: str = "settle", timeout: float = 8.0
    ) -> None:
        """
        Navigate to url. wait_until options:
          'load'    — wait for Page.frameStoppedLoading only
          'settle'  — wait for frame + a short quiet period (good enough for simple pages)
          'idle'    — wait for SPA network idle: no NEW outgoing requests for a
                      quiet window. This is the right mode for SPAs because it
                      ignores long-lived streams (the old impl counted responses
                      and never returned on pages with /api/*stream endpoints).
        """
        self._frame_stopped = False
        # Snapshot the request counter so we only observe requests that belong
        # to THIS navigation, not leftover ones from the previous page.
        req_before = self._req_sent_count
        # Fire Page.navigate without waiting for its response. On some SPA
        # transitions Chrome doesn't reply to the navigate call (the page
        # redirects or the loader stays open), and blocking would deadlock.
        # We rely on Page.frameStoppedLoading + idle polling below to know
        # when navigation actually settled.
        self._fire_and_forget("Page.navigate", {"url": url})
        deadline = time.time() + timeout

        if wait_until in ("load", "settle", "idle"):
            # First, wait for the frame to report stopped loading (HTML parsed).
            # Don't block forever if the event never comes (same-URL navigation,
            # bfcache restore, etc.) — cap at 4s, then proceed to idle polling.
            frame_deadline = time.time() + min(4.0, timeout)
            while time.time() < frame_deadline and not self._frame_stopped:
                self.drain(0.2)

        if wait_until == "idle":
            # Then, wait for SPA to stop issuing NEW requests. We poll the
            # request-sent counter; if it doesn't change across a quiet window,
            # the SPA has finished its data fetches (long-lived streams don't
            # bump the counter — only their initial request does, once).
            quiet_window = 0.6  # seconds with no new requests = "idle"
            last_change_at = time.time()
            last_count = self._req_sent_count
            while time.time() < deadline:
                self.drain(0.2)
                if self._req_sent_count != last_count:
                    last_count = self._req_sent_count
                    last_change_at = time.time()
                elif time.time() - last_change_at >= quiet_window:
                    # No new requests for `quiet_window` seconds → SPA is idle.
                    return
            # Timed out — that's OK, caller decides if it matters.

    def wait_for(
        self, js_predicate: str, timeout: float = 10.0, poll: float = 0.4
    ) -> bool:
        """
        Poll a JS predicate (must return a boolean) until it is true or timeout.
        Example: wait_for("!!document.querySelector('tbody tr')")
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval(f"Boolean({js_predicate})"):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(poll)
        return False

    def scroll(self, x: int = 0, y: int = 400, smooth: bool = True) -> None:
        """Scroll the page. `smooth=False` jumps instantly."""
        behavior = "smooth" if smooth else "instant"
        self.eval(f"window.scrollBy({{behavior:'{behavior}', left:{x}, top:{y}}})")
        time.sleep(0.4 if smooth else 0.05)

    def scroll_to_bottom(self) -> None:
        """Scroll to the very bottom of the page (useful for lazy/virtualized lists)."""
        self.eval(
            "window.scrollTo(0, document.body.scrollHeight)"
        )
        time.sleep(0.4)

    def click(
        self, selector: str, nth: int = 0, settle: float = 0.4
    ) -> bool:
        """
        Click an element matched by CSS selector via JS (most reliable for React).
        `nth` selects which match (0-based). Dispatches mouseover+mousedown+mouseup+click.
        Returns True if the element was found and clicked.
        """
        ok = self.eval(
            """
            (function() {
              const els = document.querySelectorAll(%SELECTOR%);
              const el = els[%NTH%];
              if (!el) return false;
              const opts = {bubbles:true, cancelable:true, view:window};
              for (const type of ['mouseover','mousedown','mouseup','click']) {
                el.dispatchEvent(new MouseEvent(type, opts));
              }
              return true;
            })();
            """.replace("%SELECTOR%", json.dumps(selector))
             .replace("%NTH%", str(nth))
        )
        if ok:
            self.drain(settle)
        return bool(ok)

    def hover(self, selector: str, settle: float = 0.2) -> bool:
        """Dispatch mouseover/mousemove on an element (for tooltips, dropdowns)."""
        ok = self.eval(
            """
            (function() {
              const el = document.querySelector(%SELECTOROR%);
              if (!el) return false;
              const r = el.getBoundingClientRect();
              const opts = {bubbles:true, cancelable:true, view:window,
                            clientX:r.left+r.width/2, clientY:r.top+r.height/2};
              el.dispatchEvent(new MouseEvent('mouseover', opts));
              el.dispatchEvent(new MouseEvent('mousemove', opts));
              return true;
            })();
            """.replace("%SELECTOROR%", json.dumps(selector))
        )
        if ok:
            self.drain(settle)
        return bool(ok)

    def type_text(self, selector: str, text: str, settle: float = 0.3) -> bool:
        """
        Type into an input. Uses the React-aware native setter trick so React's
        controlled components pick up the change. Appends a final key only if `text`
        ends with newline (\\n) or tab (\\t) — otherwise just fills.
        """
        submit_key = ""
        payload = text
        if text.endswith("\n"):
            submit_key = "Enter"
            payload = text[:-1]
        elif text.endswith("\t"):
            submit_key = "Tab"
            payload = text[:-1]

        ok = self.eval(
            """
            (function() {
              const el = document.querySelector(%SELECTOR%);
              if (!el) return false;
              const proto = el.tagName === 'TEXTAREA'
                ? window.HTMLTextAreaElement.prototype
                : window.HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
              setter.call(el, %PAYLOAD%);
              el.dispatchEvent(new Event('input', {bubbles:true}));
              el.dispatchEvent(new Event('change', {bubbles:true}));
              return true;
            })();
            """.replace("%SELECTOR%", json.dumps(selector))
             .replace("%PAYLOAD%", json.dumps(payload))
        )
        if not ok:
            return False
        if submit_key:
            self.dispatch_key(selector, submit_key)
        self.drain(settle)
        return True

    def dispatch_key(self, selector: str, key: str) -> None:
        """
        Dispatch a keyboard event named `key` (Enter, Tab, Escape, ArrowDown, ...).
        Resolves the element first so the event has the right target.
        """
        self.eval(
            """
            (function() {
              const el = document.querySelector(%SELECTOR%) || document.body;
              const ev = new KeyboardEvent('keydown', {
                key: %KEY%, code: %KEY%, bubbles:true, cancelable:true
              });
              el.dispatchEvent(ev);
              el.dispatchEvent(new KeyboardEvent('keyup', {
                key: %KEY%, code: %KEY%, bubbles:true, cancelable:true
              }));
            })();
            """.replace("%SELECTOR%", json.dumps(selector))
             .replace("%KEY%", json.dumps(key))
        )

    def screenshot(self, path: Optional[str] = None, full_page: bool = True) -> str:
        """
        Capture a screenshot. Returns the saved file path.
        If path is None, writes to .web-user-sim/shots/<timestamp>.png in CWD.
        """
        result = self._send_raw(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": full_page},
        )
        data = base64.b64decode(result["data"])
        if path is None:
            shots_dir = os.path.join(os.getcwd(), ".web-user-sim", "shots")
            os.makedirs(shots_dir, exist_ok=True)
            path = os.path.join(
                shots_dir, f"shot-{int(time.time() * 1000)}.png"
            )
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def dom_summary(self) -> dict:
        """
        Quick structural snapshot of the page for UX assessment:
        - title, h1 text
        - count of buttons/links/inputs
        - any visible error/empty banners
        - rough body length
        Useful to feed back to the model: "is this page actually usable right now?"
        """
        return self.eval(
            """
            (function() {
              const txt = sel => Array.from(document.querySelectorAll(sel))
                .map(e => e.innerText.trim()).filter(Boolean);
              const errSel = '[class*=error],[class*=Error],[role=alert],[class*=empty],[class*=no-data]';
              return {
                url: location.href,
                title: document.title,
                h1: (document.querySelector('h1')||{}).innerText || '',
                buttons: document.querySelectorAll('button').length,
                links: document.querySelectorAll('a').length,
                inputs: document.querySelectorAll('input').length,
                tables: document.querySelectorAll('table').length,
                tableRows: document.querySelectorAll('tbody tr').length,
                errorBanners: txt(errSel).slice(0, 5),
                bodyLen: document.body ? document.body.innerText.length : 0
              };
            })();
            """
        ) or {}

    def collect_events(self, clear: bool = True) -> list[dict]:
        """Return captured events (console/exception/network) as a list of dicts."""
        self.drain(0.4)
        out = [e.to_dict() for e in self._events]
        if clear:
            self._events.clear()
        return out

    def api_failures(self) -> list[dict]:
        """Convenience: just the /api/ network responses with status >= 400."""
        evs = self.collect_events(clear=False)
        return [
            e for e in evs
            if e["kind"] == "net_response"
            and "/api/" in e.get("url", "")
            and e.get("status", 0) >= 400
        ]

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


# Convenience for ad-hoc scripts: `python cdp_lib.py http://localhost:5173/`
if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173/"
    port = int(os.environ.get("WEB_USER_SIM_PORT", "9222"))
    sess = CDPSession.connect(port=port, auto_open=target_url)
    try:
        sess.goto(target_url, wait_until="idle")
        print(json.dumps(sess.dom_summary(), indent=2, ensure_ascii=False))
        print("--- events ---")
        print(json.dumps(sess.collect_events(), indent=2, ensure_ascii=False))
        shot = sess.screenshot()
        print(f"--- screenshot saved: {shot}")
    finally:
        sess.close()
