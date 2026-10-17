"""Реестр сетапов: жизненный цикл, события, исходы.

Детекторы раз в цикл отдают кандидатов своего типа (``sync``). Реестр:
  * открывает идею, когда кандидат держится ``min_persist_sec`` (отсекает
    одиночные тики-выбросы);
  * обновляет — не чаще ``update_min_interval_sec`` при заметном изменении и
    раз в ``heartbeat_sec`` в любом случае;
  * закрывает conditional-идею, если кандидата нет дольше ``grace_sec``;
  * для sticky-идей отслеживает цену (``track``): стоп, цели, истечение срока;
  * после закрытия держит ``cooldown_sec``, чтобы идея не «мигала».

Каждое изменение — событие ``{"seq", "event", "ts", "setup"}``; кольцевой
буфер событий позволяет подписчикам дочитать пропущенное по seq.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from mexc_monitor.setups.model import SETUP_TYPES, Candidate, Setup, iso

logger = logging.getLogger(__name__)

Listener = Callable[[dict[str, Any]], None]
PriceFn = Callable[[str, str], float | None]  # (venue, base) -> цена за монету


@dataclass(slots=True)
class TypePolicy:
    min_persist_sec: float = 0.0
    grace_sec: float = 3.0
    cooldown_sec: float = 0.0
    update_min_interval_sec: float = 1.0
    heartbeat_sec: float = 15.0
    score_change: float = 5.0
    edge_change_bps: float = 2.0


DEFAULT_POLICIES: dict[str, TypePolicy] = {
    "cross_perp": TypePolicy(min_persist_sec=1.5, grace_sec=3.0, cooldown_sec=10.0),
    "funding_arb": TypePolicy(min_persist_sec=0.0, grace_sec=120.0, cooldown_sec=300.0, heartbeat_sec=60.0),
    "basis": TypePolicy(min_persist_sec=2.0, grace_sec=5.0, cooldown_sec=30.0),
    "lead_lag": TypePolicy(cooldown_sec=30.0),
    "breakout": TypePolicy(cooldown_sec=1800.0),
    "momentum": TypePolicy(cooldown_sec=1800.0),
    "funding_squeeze": TypePolicy(cooldown_sec=4 * 3600.0),
    "btc_lag": TypePolicy(cooldown_sec=1800.0),
}


class SetupRegistry:
    def __init__(self, policies: dict[str, TypePolicy] | None = None, *, ring_size: int = 10_000) -> None:
        self.policies = dict(DEFAULT_POLICIES)
        if policies:
            self.policies.update(policies)
        self._lock = threading.RLock()
        self._active: dict[str, Setup] = {}
        self._pending: dict[str, float] = {}  # key -> first_seen
        self._last_seen: dict[str, float] = {}
        self._last_emit: dict[str, float] = {}
        self._cooldown: dict[str, float] = {}  # key -> until
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._seq = 0
        self._listeners: list[Listener] = []
        self._closed: dict[str, deque[Setup]] = {t: deque(maxlen=2000) for t in SETUP_TYPES}
        self._opened_count: dict[str, int] = {t: 0 for t in SETUP_TYPES}

    # ───────────────────────────────────────────────────── subscriptions

    def subscribe(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _emit(self, event: str, s: Setup, now: float) -> dict[str, Any]:
        self._seq += 1
        ev = {"seq": self._seq, "event": event, "ts": iso(now), "setup": s.to_dict()}
        self._ring.append(ev)
        self._last_emit[s.key] = now
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 — подписчик не должен ломать реестр
                logger.exception("setup listener failed")
        return ev

    @property
    def seq(self) -> int:
        return self._seq

    def events_since(self, seq: int, limit: int = 5000) -> tuple[list[dict[str, Any]], bool]:
        """События с seq > заданного. Второе значение — False, если часть уже вытеснена."""
        with self._lock:
            if not self._ring:
                return [], True
            complete = seq >= self._ring[0]["seq"] - 1
            out = [e for e in self._ring if e["seq"] > seq]
            return out[-limit:], complete

    # ───────────────────────────────────────────────────── sync

    def sync(self, setup_type: str, candidates: list[Candidate], now: float | None = None) -> list[dict[str, Any]]:
        """Применить кандидатов одного типа (всех, что детектор видит в этом цикле)."""
        now = time.time() if now is None else now
        policy = self.policies[setup_type]
        sticky = SETUP_TYPES[setup_type][1] == "sticky"
        events: list[dict[str, Any]] = []
        with self._lock:
            seen: set[str] = set()
            for c in candidates:
                if c.type != setup_type:
                    continue
                seen.add(c.key)
                self._last_seen[c.key] = now
                active = self._active.get(c.key)
                if active is not None:
                    if not sticky:
                        ev = self._maybe_update(active, c, policy, now)
                        if ev is not None:
                            events.append(ev)
                    continue
                if not c.opens or self._cooldown.get(c.key, 0.0) > now:
                    self._pending.pop(c.key, None)
                    continue
                first = self._pending.setdefault(c.key, now)
                if now - first >= policy.min_persist_sec:
                    events.append(self._open(c, now))
            # кандидаты, которых больше нет: pending сбрасываем, conditional закрываем после grace
            for key in [k for k, t in self._pending.items() if k not in seen and k.startswith(setup_type + ":")]:
                del self._pending[key]
            if not sticky:
                for key, s in list(self._active.items()):
                    if s.type != setup_type or key in seen:
                        continue
                    if now - self._last_seen.get(key, s.opened_at) >= policy.grace_sec:
                        events.append(self._close(s, "condition_gone", now))
        return events

    def _open(self, c: Candidate, now: float) -> dict[str, Any]:
        s = Setup(candidate=c, opened_at=now, updated_at=now)
        if c.ttl_sec:
            s.valid_until = now + c.ttl_sec
        s.max_edge_bps = c.edge_bps
        s.max_score = c.score
        s.last_price = c.entry
        self._active[c.key] = s
        self._pending.pop(c.key, None)
        self._opened_count[c.type] = self._opened_count.get(c.type, 0) + 1
        return self._emit("opened", s, now)

    def _maybe_update(self, s: Setup, c: Candidate, policy: TypePolicy, now: float) -> dict[str, Any] | None:
        prev = s.candidate
        s.candidate = c
        if c.edge_bps is not None and (s.max_edge_bps is None or c.edge_bps > s.max_edge_bps):
            s.max_edge_bps = c.edge_bps
        s.max_score = max(s.max_score, c.score)
        since = now - self._last_emit.get(s.key, 0.0)
        material = abs(c.score - prev.score) >= policy.score_change or (
            c.edge_bps is not None
            and prev.edge_bps is not None
            and abs(c.edge_bps - prev.edge_bps) >= policy.edge_change_bps
        ) or c.opens != prev.opens
        if (material and since >= policy.update_min_interval_sec) or since >= policy.heartbeat_sec:
            s.updated_at = now
            return self._emit("updated", s, now)
        return None

    def _close(self, s: Setup, reason: str, now: float, outcome: dict[str, Any] | None = None) -> dict[str, Any]:
        s.status = "closed"
        s.closed_at = now
        s.close_reason = reason
        s.updated_at = now
        if outcome is None:
            outcome = {"duration_sec": round(now - s.opened_at, 1)}
            if s.policy == "conditional":
                outcome["max_edge_bps"] = round(s.max_edge_bps, 2) if s.max_edge_bps is not None else None
                outcome["edge_at_close_bps"] = round(s.candidate.edge_bps, 2) if s.candidate.edge_bps is not None else None
        s.outcome = outcome
        self._active.pop(s.key, None)
        self._closed[s.type].append(s)
        policy = self.policies[s.type]
        if policy.cooldown_sec > 0:
            self._cooldown[s.key] = now + policy.cooldown_sec
        return self._emit("closed", s, now)

    # ───────────────────────────────────────────────────── sticky tracking

    def track(self, price_fn: PriceFn, now: float | None = None) -> list[dict[str, Any]]:
        """Сверка sticky-идей с ценой: стоп, цели, срок. Вызывается каждый цикл."""
        now = time.time() if now is None else now
        events: list[dict[str, Any]] = []
        with self._lock:
            for s in list(self._active.values()):
                if s.policy != "sticky":
                    continue
                c = s.candidate
                price = price_fn(c.track_venue or c.legs[0].venue, c.base) if c.entry else None
                if price is not None and c.entry:
                    s.last_price = price
                    sign = 1.0 if c.direction == "long" else -1.0
                    ret = sign * (price / c.entry - 1.0) * 100
                    s.mfe_pct = max(s.mfe_pct, ret)
                    s.mae_pct = min(s.mae_pct, ret)
                    hit = sum(1 for t in c.targets if sign * (price - t) >= 0)
                    s.targets_hit = max(s.targets_hit, hit)
                    if c.stop is not None and sign * (price - c.stop) <= 0:
                        events.append(self._close(s, "stop_hit", now, self._sticky_outcome(s, "stop", price, now)))
                        continue
                    if c.targets and s.targets_hit >= len(c.targets):
                        events.append(self._close(s, "target_hit", now, self._sticky_outcome(s, "target", price, now)))
                        continue
                if s.valid_until is not None and now >= s.valid_until:
                    result = "target" if s.targets_hit > 0 else "expired"
                    events.append(self._close(s, "expired", now, self._sticky_outcome(s, result, s.last_price, now)))
        return events

    @staticmethod
    def _sticky_outcome(s: Setup, result: str, price: float | None, now: float) -> dict[str, Any]:
        c = s.candidate
        sign = 1.0 if c.direction == "long" else -1.0
        ret = sign * (price / c.entry - 1.0) * 100 if price and c.entry else None
        risk_pct = abs(c.entry - c.stop) / c.entry * 100 if c.entry and c.stop else None
        return {
            "result": result,
            "targets_hit": s.targets_hit,
            "return_pct": round(ret, 4) if ret is not None else None,
            "r_multiple": round(ret / risk_pct, 3) if ret is not None and risk_pct else None,
            "mfe_pct": round(s.mfe_pct, 4),
            "mae_pct": round(s.mae_pct, 4),
            "exit_price": price,
            "duration_sec": round(now - s.opened_at, 1),
        }

    # ───────────────────────────────────────────────────── queries

    def active(self) -> list[Setup]:
        with self._lock:
            return list(self._active.values())

    def get(self, key: str) -> Setup | None:
        with self._lock:
            return self._active.get(key)

    def recently_closed(self, limit: int = 200) -> list[Setup]:
        with self._lock:
            out = [s for q in self._closed.values() for s in q]
        out.sort(key=lambda s: s.closed_at or 0.0, reverse=True)
        return out[:limit]

    def stats(self) -> dict[str, Any]:
        """Сводка по типам: сколько открыто/закрыто, исходы sticky-идей."""
        with self._lock:
            out: dict[str, Any] = {}
            for t, closed in self._closed.items():
                active = sum(1 for s in self._active.values() if s.type == t)
                row: dict[str, Any] = {"opened": self._opened_count.get(t, 0), "active": active, "closed": len(closed)}
                if SETUP_TYPES[t][1] == "sticky":
                    results = [s.outcome or {} for s in closed]
                    wins = sum(1 for o in results if o.get("result") == "target")
                    losses = sum(1 for o in results if o.get("result") == "stop")
                    rs = [o["r_multiple"] for o in results if o.get("r_multiple") is not None]
                    row.update(
                        wins=wins,
                        losses=losses,
                        expired=sum(1 for o in results if o.get("result") == "expired"),
                        win_rate=round(wins / (wins + losses), 3) if wins + losses else None,
                        avg_r=round(sum(rs) / len(rs), 3) if rs else None,
                    )
                else:
                    durs = [s.outcome.get("duration_sec", 0.0) for s in closed if s.outcome]
                    row["avg_duration_sec"] = round(sum(durs) / len(durs), 1) if durs else None
                out[t] = row
            return out
