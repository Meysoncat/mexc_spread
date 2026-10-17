"""Журнал событий сетапов в SQLite: история для потребителей и калибровки порогов.

Пишет фоновый поток пачками, чтобы цикл детекторов не ждал диск.
По умолчанию сохраняются opened и closed (с исходом); updated — опционально.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_events (
    seq INTEGER NOT NULL,
    epoch REAL NOT NULL,
    event TEXT NOT NULL,
    id TEXT NOT NULL,
    instance TEXT NOT NULL,
    type TEXT NOT NULL,
    base TEXT NOT NULL,
    score REAL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_setup_events_epoch ON setup_events (epoch);
CREATE INDEX IF NOT EXISTS ix_setup_events_type_epoch ON setup_events (type, epoch);
CREATE INDEX IF NOT EXISTS ix_setup_events_instance ON setup_events (instance);
"""


class SetupStore:
    def __init__(self, path: str | Path, *, persist_updates: bool = False, retention_days: int = 30) -> None:
        self.path = Path(path)
        self.persist_updates = persist_updates
        self.retention_days = retention_days
        self._q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=50_000)
        self._thread: threading.Thread | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ───────────────────────────────────────────── writer

    def on_event(self, ev: dict[str, Any]) -> None:
        if ev["event"] == "updated" and not self.persist_updates:
            return
        try:
            self._q.put_nowait(ev)
        except queue.Full:
            logger.warning("setup store queue full, event %s dropped", ev.get("seq"))

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="setup-store", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._q.put(None)

    def _run(self) -> None:
        conn = self._connect()
        last_cleanup = 0.0
        while True:
            batch: list[dict[str, Any]] = []
            item = self._q.get()
            if item is None:
                break
            batch.append(item)
            while len(batch) < 500:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self._write(conn, batch)
                    conn.close()
                    return
                batch.append(item)
            self._write(conn, batch)
            if time.time() - last_cleanup > 3600:
                last_cleanup = time.time()
                self._cleanup(conn)
        conn.close()

    def _write(self, conn: sqlite3.Connection, batch: list[dict[str, Any]]) -> None:
        rows = []
        for ev in batch:
            s = ev["setup"]
            rows.append((ev["seq"], time.time(), ev["event"], s["id"], s["instance"], s["type"], s["base"], s.get("score"), json.dumps(ev, separators=(",", ":"))))
        try:
            with conn:
                conn.executemany("INSERT INTO setup_events VALUES (?,?,?,?,?,?,?,?,?)", rows)
        except sqlite3.Error as e:
            logger.error("setup store write failed: %s", e)

    def _cleanup(self, conn: sqlite3.Connection) -> None:
        cutoff = time.time() - self.retention_days * 86400
        try:
            with conn:
                conn.execute("DELETE FROM setup_events WHERE epoch < ?", (cutoff,))
        except sqlite3.Error as e:
            logger.warning("setup store cleanup failed: %s", e)

    # ───────────────────────────────────────────── reads

    def history(
        self,
        *,
        event: str | None = "closed",
        setup_type: str | None = None,
        base: str | None = None,
        since_epoch: float | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM setup_events WHERE 1=1"
        args: list[Any] = []
        if event:
            sql += " AND event = ?"
            args.append(event)
        if setup_type:
            sql += " AND type = ?"
            args.append(setup_type)
        if base:
            sql += " AND base = ?"
            args.append(base.upper())
        if since_epoch is not None:
            sql += " AND epoch >= ?"
            args.append(since_epoch)
        sql += " ORDER BY epoch DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(sql, args)]

    def outcome_stats(self, since_epoch: float) -> dict[str, Any]:
        """Сводка исходов закрытых sticky-идей по типам за период."""
        out: dict[str, Any] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT type, payload FROM setup_events WHERE event = 'closed' AND epoch >= ?",
                (since_epoch,),
            ).fetchall()
        for setup_type, payload in rows:
            o = (json.loads(payload).get("setup") or {}).get("outcome") or {}
            row = out.setdefault(setup_type, {"closed": 0, "wins": 0, "losses": 0, "expired": 0, "r_sum": 0.0, "r_n": 0})
            row["closed"] += 1
            res = o.get("result")
            if res == "target":
                row["wins"] += 1
            elif res == "stop":
                row["losses"] += 1
            elif res == "expired":
                row["expired"] += 1
            if o.get("r_multiple") is not None:
                row["r_sum"] += o["r_multiple"]
                row["r_n"] += 1
        for row in out.values():
            decided = row["wins"] + row["losses"]
            row["win_rate"] = round(row["wins"] / decided, 3) if decided else None
            row["avg_r"] = round(row["r_sum"] / row["r_n"], 3) if row["r_n"] else None
            del row["r_sum"], row["r_n"]
        return out
