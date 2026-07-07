"""Generic atomic JSON state persistence for trading engines.

Provides a reusable :class:`StateStore` that writes state to disk atomically
(write to ``.tmp`` then ``os.replace``) so a crash mid-write cannot corrupt
the state file. Engines that need to survive restarts (spread_capture,
arbitrage) use this instead of reimplementing serialization each time.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class StateStore:
    """Atomic JSON persistence for engine state.

    Parameters
    ----------
    path
        File path for the state JSON.
    alert_callback
        Optional ``callback(error_msg: str)`` invoked when the state file
        is corrupt and the engine must start fresh.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        alert_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._path = Path(path)
        self._alert_callback = alert_callback

    @property
    def path(self) -> Path:
        return self._path

    def save(self, data: dict[str, Any]) -> bool:
        """Atomically save *data* to the state file.

        Returns ``True`` on success, ``False`` on I/O error.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
            os.replace(str(tmp), str(self._path))
            return True
        except OSError as e:
            logger.error("StateStore save failed (%s): %s", self._path, e)
            return False

    def load(self) -> dict[str, Any] | None:
        """Load state from file.

        Returns the parsed dict, or ``None`` if the file does not exist.
        If the file exists but is corrupt, logs an error, invokes the alert
        callback, and returns ``None`` (start fresh).
        """
        if not self._path.is_file():
            return None

        try:
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("state root is not a dict")
            return data
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("StateStore load failed (%s): %s", self._path, e)
            if self._alert_callback:
                try:
                    self._alert_callback(str(e))
                except Exception:
                    pass
            return None

    def clear(self) -> None:
        """Remove the state file if it exists."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


def _json_default(obj: Any) -> Any:
    """JSON serializer for dataclasses and other common types."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def serialize_dataclass_list(items: list[Any]) -> list[dict[str, Any]]:
    """Convert a list of dataclass instances to a list of dicts."""
    return [asdict(item) if is_dataclass(item) else dict(item) for item in items]
