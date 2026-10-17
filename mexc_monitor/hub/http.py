"""Тонкая обёртка над общим httpx-пулом: JSON или понятная ошибка."""

from __future__ import annotations

from typing import Any

import httpx

from mexc_monitor.http_shared import shared_get

_post_client: httpx.Client | None = None


class VenueHttpError(RuntimeError):
    pass


def get_json(url: str, params: dict[str, Any] | None = None, *, timeout: float = 10.0) -> Any:
    try:
        r = shared_get(url, params=params, timeout=timeout)
    except httpx.HTTPError as e:
        raise VenueHttpError(f"{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise VenueHttpError(f"HTTP {r.status_code} {url}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise VenueHttpError(f"bad JSON from {url}") from e


def post_json(url: str, body: dict[str, Any], *, timeout: float = 10.0) -> Any:
    global _post_client
    if _post_client is None or _post_client.is_closed:
        _post_client = httpx.Client()
    try:
        r = _post_client.post(url, json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise VenueHttpError(f"{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise VenueHttpError(f"HTTP {r.status_code} {url}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise VenueHttpError(f"bad JSON from {url}") from e


def fnum(v: Any) -> float | None:
    """Число из строки/числа биржи; пустое и мусор → None."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN → None


def inum(v: Any) -> int | None:
    x = fnum(v)
    return int(x) if x is not None else None
