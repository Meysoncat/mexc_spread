"""Signal Worker — фоновый сканер watchlist для стратегии ProBoyScalp.

Раз в ``interval_sec`` секунд прогоняет список тикеров (watchlist) через
:class:`MetaScalpDensityScanner` + :class:`ParticipantDetector`. Для тикеров,
где найдены И стены, И участник (или только стены — без участника алерт
тише), шлёт Telegram-алерт через :class:`AlertService`.

Хранит последний снимок сигналов в in-memory ring buffer, чтобы UI мог
опрашивать ``/api/metascalp/signals`` без нагрузки на MetaScalp.

Watchlist и пороги загружаются из ``config/metascalp_signals.json``.
См. ``docs/PROBOYSCALP_METHOD.md`` для контекста стратегии.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mexc_monitor.metascalp.density_scanner import DensityScan, MetaScalpDensityScanner
from mexc_monitor.metascalp.participant_detector import (
    ParticipantDetector,
    ParticipantSignal,
)

logger = logging.getLogger(__name__)


# Путь к конфигу watchlist (переопределяется через env MEXC_METASCALP_SIGNALS_CONFIG)
_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "metascalp_signals.json"
)

DEFAULT_INTERVAL_SEC = 10.0
DEFAULT_ALERT_MIN_WALLS = 1  # минимум стен для алерта
DEFAULT_ALERT_MIN_PARTICIPANT_VOL = 1000.0  # USDT в spike-окне
DEFAULT_TOP_SIGNALS_KEEP = 50  # размер ring buffer последних сигналов


@dataclass
class CombinedSignal:
    """Комбинированный сигнал (стены + участник)."""

    timestamp_ms: int
    conn_id: str
    ticker: str
    # Из DensityScan
    walls_count: int
    max_wall_notional_usdt: float
    spread_bps: float
    best_bid: float
    best_ask: float
    # Из ParticipantSignal (может не быть)
    participant_direction: str = ""  # "" если нет
    participant_spike_vol_usdt: float = 0.0
    participant_is_strong: bool = False
    participant_dom_ratio: float = 0.0
    # Классификация
    score: float = 0.0  # для сортировки

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "conn_id": self.conn_id,
            "ticker": self.ticker,
            "walls_count": self.walls_count,
            "max_wall_notional_usdt": round(self.max_wall_notional_usdt, 2),
            "spread_bps": round(self.spread_bps, 2),
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "participant_direction": self.participant_direction,
            "participant_spike_vol_usdt": round(self.participant_spike_vol_usdt, 2),
            "participant_is_strong": self.participant_is_strong,
            "participant_dom_ratio": round(self.participant_dom_ratio, 3),
            "score": round(self.score, 2),
        }


def _load_config(path: Path) -> dict[str, Any]:
    """Загрузить конфиг watchlist. Если файла нет — вернуть дефолты."""
    defaults: dict[str, Any] = {
        "enabled": False,
        "conn_id": "",  # если пусто — берём первое доступное connection
        "watchlist": [],
        "interval_sec": DEFAULT_INTERVAL_SEC,
        "min_notional_usdt": 1_000.0,
        "multiplier": 5.0,
        "min_spread_bps": 5.0,
        "alert_min_walls": DEFAULT_ALERT_MIN_WALLS,
        "alert_min_participant_vol": DEFAULT_ALERT_MIN_PARTICIPANT_VOL,
        "spike_threshold_usdt": 1_000.0,
        "spike_window_sec": 60.0,
        "cluster_threshold_usdt": 5_000.0,
        "cluster_window_sec": 900.0,
        "domination_ratio": 0.65,
    }
    if not path.is_file():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            defaults.update(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("signal_worker: config load failed (%s): %s", path, e)
    return defaults


class SignalWorker:
    """Фоновый воркер: периодически сканирует watchlist и алертит.

    Жизненный цикл::

        worker = SignalWorker(client, alert_service, config_path=...)
        worker.start()             # запускает фоновый поток
        worker.get_top_signals()   # для UI
        worker.stop()
    """

    def __init__(
        self,
        client: Any,  # MetaScalpClient — избегаем жёсткой зависимости на импорт для типизации
        alert_service: Any | None = None,
        participant_detector: ParticipantDetector | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self._client = client
        self._alert_service = alert_service
        self._config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        # Детектор участников: либо переданный снаружи (shared с ws_bridge),
        # либо собственный (тогда trades в него не идут — будет пустой).
        self._participant_detector = participant_detector or ParticipantDetector()

        self._scanner: MetaScalpDensityScanner | None = None  # lazy init после загрузки конфига
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Ring buffer последних сигналов (для UI)
        self._recent_signals: deque[CombinedSignal] = deque(
            maxlen=DEFAULT_TOP_SIGNALS_KEEP
        )
        # Последний полный снимок скана (включая некандидатов)
        self._last_scans: list[DensityScan] = []

        self._config = _load_config(self._config_path)

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Запустить фоновый сканер. Возвращает True если запустился."""
        if not self._config.get("enabled", False):
            logger.info("SignalWorker: disabled by config (enabled=False)")
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._scanner = self._build_scanner()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="metascalp-signal-worker", daemon=True
            )
            self._thread.start()
            logger.info(
                "SignalWorker started (interval=%.1fs, watchlist=%d)",
                self._config.get("interval_sec", DEFAULT_INTERVAL_SEC),
                len(self._config.get("watchlist", [])),
            )
            return True

    def stop(self) -> None:
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        logger.info("SignalWorker stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def reload_config(self) -> dict[str, Any]:
        """Перезагрузить конфиг. Если воркер был включён — рестарт."""
        was_running = self.is_running()
        if was_running:
            self.stop()
        self._config = _load_config(self._config_path)
        if was_running:
            self.start()
        return self.get_config()

    # ─── Public API для эндпоинтов ─────────────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        """Текущий конфиг (безопасный для UI)."""
        return dict(self._config)

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Обновить отдельные поля конфига и сохранить на диск."""
        for k, v in patch.items():
            if k in self._config:
                self._config[k] = v
        self._save_config()
        return self.get_config()

    def get_top_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        """Последние сигналы из ring buffer (для UI)."""
        with self._lock:
            items = list(self._recent_signals)
        items.sort(key=lambda s: s.score, reverse=True)
        return [s.to_dict() for s in items[:limit]]

    def get_last_scan(self) -> list[dict[str, Any]]:
        """Последний полный снимок скана (все тикеры watchlist)."""
        with self._lock:
            return [s.to_dict() for s in self._last_scans]

    def scan_now(self) -> list[CombinedSignal]:
        """Принудительно сканировать watchlist сейчас (для ручного эндпоинта)."""
        if self._scanner is None:
            self._scanner = self._build_scanner()
        return self._run_once()

    # ─── Internal ─────────────────────────────────────────────────────────

    def _build_scanner(self) -> MetaScalpDensityScanner:
        return MetaScalpDensityScanner(
            self._client,
            min_notional_usdt=float(self._config.get("min_notional_usdt", 1_000.0)),
            multiplier=float(self._config.get("multiplier", 5.0)),
            min_spread_bps=float(self._config.get("min_spread_bps", 5.0)),
        )

    def _resolve_conn_id(self) -> str:
        """Найти conn_id: из конфига или первое доступное connection."""
        cid = str(self._config.get("conn_id", "")).strip()
        if cid:
            return cid
        try:
            conns = self._client.connections()
            for c in conns or []:
                # Берём первое Connected подключение
                if getattr(c, "status", "").lower() in ("connected", "active", ""):
                    return c.id
            if conns:
                return conns[0].id
        except Exception as e:
            logger.warning("SignalWorker: connections() failed: %s", e)
        return ""

    def _loop(self) -> None:
        interval = float(self._config.get("interval_sec", DEFAULT_INTERVAL_SEC))
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except Exception:
                logger.exception("SignalWorker loop iteration failed")
            # Перезарядка interval (на случай если конфиг изменили)
            interval = float(self._config.get("interval_sec", DEFAULT_INTERVAL_SEC))
            self._stop_event.wait(timeout=interval)

    def _run_once(self) -> list[CombinedSignal]:
        """Один проход: скан watchlist → combined signals → алерты."""
        if self._scanner is None:
            return []
        conn_id = self._resolve_conn_id()
        if not conn_id:
            return []
        watchlist = [str(t).strip().upper() for t in self._config.get("watchlist", []) if str(t).strip()]
        if not watchlist:
            return []

        # Скан стаканов (all, не только кандидатов — UI хочет видеть статус)
        scans = self._scanner.scan_watchlist(
            conn_id, watchlist, only_candidates=False, push_history=True
        )
        with self._lock:
            self._last_scans = list(scans)

        # Снимаем participant-сигналы для всех тикеров разом
        participants: dict[str, ParticipantSignal] = {}
        for sig in self._participant_detector.detect_all(watchlist):
            participants[sig.ticker] = sig

        # Комбинируем
        combined: list[CombinedSignal] = []
        alert_min_walls = int(self._config.get("alert_min_walls", DEFAULT_ALERT_MIN_WALLS))
        alert_min_vol = float(
            self._config.get("alert_min_participant_vol", DEFAULT_ALERT_MIN_PARTICIPANT_VOL)
        )
        for scan in scans:
            if not scan.walls:
                continue
            p = participants.get(scan.ticker)
            max_wall = max((w.notional_usdt for w in scan.walls), default=0.0)
            p_dir = p.direction if p else ""
            p_vol = p.spike_volume_usdt if p else 0.0
            p_strong = bool(p and p.is_strong)
            p_ratio = p.domination_ratio if p else 0.0

            # Score: стены × объём участника × спред
            score = self._compute_score(
                walls_count=len(scan.walls),
                max_wall_notional=max_wall,
                spread_bps=scan.spread_bps,
                participant_vol=p_vol,
                participant_strong=p_strong,
            )

            cs = CombinedSignal(
                timestamp_ms=scan.timestamp_ms,
                conn_id=conn_id,
                ticker=scan.ticker,
                walls_count=len(scan.walls),
                max_wall_notional_usdt=max_wall,
                spread_bps=scan.spread_bps,
                best_bid=scan.best_bid,
                best_ask=scan.best_ask,
                participant_direction=p_dir,
                participant_spike_vol_usdt=p_vol,
                participant_is_strong=p_strong,
                participant_dom_ratio=p_ratio,
                score=score,
            )
            combined.append(cs)

        combined.sort(key=lambda s: s.score, reverse=True)

        # Сохранить в ring buffer
        with self._lock:
            for c in combined:
                self._recent_signals.append(c)

        # Разослать алерты (только для кандидатов: стены + достаточный участник или просто много стен)
        if self._alert_service is not None:
            for c in combined:
                enough_participant = bool(c.participant_direction) and c.participant_spike_vol_usdt >= alert_min_vol
                # Шлём если: есть участник ИЛИ очень много стен (≥ 2× порога)
                if not (enough_participant or c.walls_count >= max(alert_min_walls * 2, 2)):
                    continue
                try:
                    self._alert_service.send_density_signal(
                        ticker=c.ticker,
                        walls_count=c.walls_count,
                        max_wall_notional_usdt=c.max_wall_notional_usdt,
                        spread_bps=c.spread_bps,
                        participant_direction=c.participant_direction or None,
                        participant_volume_usdt=c.participant_spike_vol_usdt,
                        participant_is_strong=c.participant_is_strong,
                    )
                except Exception:
                    logger.exception("SignalWorker: alert send failed for %s", c.ticker)

        return combined

    @staticmethod
    def _compute_score(
        *,
        walls_count: int,
        max_wall_notional: float,
        spread_bps: float,
        participant_vol: float,
        participant_strong: bool,
    ) -> float:
        """Эвристический score для сортировки сигналов.

        Больше = интереснее. Учитывает:
        - количество стен (линейно)
        - размер крупнейшей стены (log, чтобы одна жирная стена не доминировала)
        - ширину спреда (это потенциал заработка)
        - объём участника (участник = подтверждение активности)
        """
        import math

        wall_score = walls_count * 10.0
        size_score = math.log10(max(max_wall_notional, 1.0)) * 5.0
        spread_score = max(spread_bps, 0.0)
        participant_score = participant_vol / 200.0 + (50.0 if participant_strong else 0.0)
        return wall_score + size_score + spread_score + participant_score

    def _save_config(self) -> None:
        """Сохранить текущий конфиг на диск."""
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(
                json.dumps(self._config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("SignalWorker: config save failed: %s", e)
