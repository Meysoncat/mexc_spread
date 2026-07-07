from __future__ import annotations

import logging
import threading

from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.history_store import append_snapshot, resolve_history_db_path
from mexc_monitor.pipeline import safe_load_snapshot

logger = logging.getLogger(__name__)

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _loop() -> None:
    while not _stop.is_set():
        s = DEFAULT_SETTINGS
        interval = max(5.0, float(s.history_interval_sec)) if s.history_enabled else 30.0
        if s.history_enabled:
            try:
                path = resolve_history_db_path(s)
                for m in s.history_markets:
                    if _stop.is_set():
                        break
                    if m not in ("spot", "futures"):
                        continue
                    df, err = safe_load_snapshot(m, s)
                    if err:
                        logger.warning("history snapshot %s: %s", m, err)
                        continue
                    if df is None or df.empty:
                        continue
                    n = append_snapshot(path, m, df)
                    logger.debug("history stored %s rows market=%s", n, m)
            except Exception:
                logger.exception("history tick failed")
        if _stop.wait(timeout=interval):
            break


def start_history_worker() -> None:
    global _thread
    if not DEFAULT_SETTINGS.history_enabled:
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        t = threading.Thread(target=_loop, daemon=True, name="mexc-history")
        _thread = t
    t.start()


def stop_history_worker() -> None:
    _stop.set()
