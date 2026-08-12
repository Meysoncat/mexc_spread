"""Cross-exchange coin transfer networks (deposit/withdraw availability).

Only exchanges with *public* (keyless) currency/chain endpoints are supported:
Gate.io and Bitget. Other venues (Binance, Bybit, OKX, MEXC) expose this only
through signed endpoints, so they are intentionally omitted — the frontend
treats a leg on an unsupported exchange as "unverified" rather than inventing
data.

Chain names differ per exchange (Gate calls BSC "BSC", Bitget calls it
"BEP20"), so raw names are canonicalized to a shared label before comparison.
Results are cached in-process with a TTL because currency configs change rarely.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.http_utils import mexc_httpx_client

logger = logging.getLogger(__name__)

# Exchanges with keyless network data.
SUPPORTED_EXCHANGES: tuple[str, ...] = ("gateio", "bitget")

_GATEIO_URL = "https://api.gateio.ws/api/v4/spot/currencies"
_BITGET_URL = "https://api.bitget.com/api/v2/spot/public/coins"

# Cache TTL — currency/chain configs are near-static.
_CACHE_TTL_SEC = 30 * 60

# ─── Network name canonicalization ──────────────────────────────────────────
# Map every known exchange-specific alias to a shared, human-friendly label so
# that a network present on two exchanges compares equal. Unknown names fall
# back to a normalized (uppercased, punctuation-stripped) form.
_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "ERC20": ("ETH", "ERC20", "ETHEREUM"),
    "BEP20": ("BSC", "BEP20", "BNBSMARTCHAIN", "BEP2E"),
    "TRC20": ("TRX", "TRC20", "TRON"),
    "SOL": ("SOL", "SOLANA", "SPL"),
    "ARBITRUM": ("ARBEVM", "ARBITRUMONE", "ARBITRUM", "ARB"),
    "OPTIMISM": ("OPETH", "OPTIMISM", "OP"),
    "POLYGON": ("MATIC", "POLYGON", "POL"),
    "AVAX-C": ("AVAXC", "AVAX_C", "AVAXCCHAIN", "AVALANCHECCHAIN", "AVAXCCHAIN"),
    "TON": ("TON", "TONCOIN"),
    "APTOS": ("APT", "APTOS"),
    "BTC": ("BTC", "BITCOIN"),
    "NEAR": ("NEAR",),
    "ALGO": ("ALGO", "ALGORAND"),
    "EOS": ("EOS",),
    "TEZOS": ("XTZ", "TEZOS"),
}

# Reverse lookup: normalized alias -> canonical label.
_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in _ALIAS_GROUPS.items():
    for _a in _aliases:
        _ALIAS_TO_CANON[_a] = _canon


def canonical_network(name: str) -> str:
    """Return a shared label for an exchange-specific chain name."""
    if not name:
        return ""
    norm = "".join(ch for ch in name.upper() if ch.isalnum())
    return _ALIAS_TO_CANON.get(norm, norm)


# ─── Cache ──────────────────────────────────────────────────────────────────
_lock = threading.Lock()
# exchange -> (fetched_at, {coin: [{network, deposit, withdraw, raw}]})
_cache: dict[str, tuple[float, dict[str, list[dict[str, Any]]]]] = {}


def _normalize_chain_list(
    chains: list[dict[str, Any]],
    *,
    name_key: str,
    withdraw_fn: Any,
    deposit_fn: Any,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ch in chains:
        raw = str(ch.get(name_key, "") or "")
        canon = canonical_network(raw)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        out.append(
            {
                "network": canon,
                "raw": raw,
                "withdraw": bool(withdraw_fn(ch)),
                "deposit": bool(deposit_fn(ch)),
            }
        )
    return out


def _fetch_gateio() -> dict[str, list[dict[str, Any]]]:
    with mexc_httpx_client(DEFAULT_SETTINGS, exchange="gateio") as c:
        r = c.get(_GATEIO_URL, timeout=15.0)
        r.raise_for_status()
        data = r.json()
    result: dict[str, list[dict[str, Any]]] = {}
    for cur in data:
        coin = str(cur.get("currency", "")).upper()
        if not coin:
            continue
        chains = cur.get("chains") or []
        result[coin] = _normalize_chain_list(
            chains,
            name_key="name",
            # Gate uses *_disabled flags (True == blocked).
            withdraw_fn=lambda ch: not ch.get("withdraw_disabled", False),
            deposit_fn=lambda ch: not ch.get("deposit_disabled", False),
        )
    return result


def _fetch_bitget() -> dict[str, list[dict[str, Any]]]:
    with mexc_httpx_client(DEFAULT_SETTINGS, exchange="bitget") as c:
        r = c.get(_BITGET_URL, timeout=15.0)
        r.raise_for_status()
        payload = r.json()
    data = payload.get("data") or []
    result: dict[str, list[dict[str, Any]]] = {}
    for cur in data:
        coin = str(cur.get("coin", "")).upper()
        if not coin:
            continue
        chains = cur.get("chains") or []
        result[coin] = _normalize_chain_list(
            chains,
            name_key="chain",
            # Bitget uses positive string flags "true"/"false".
            withdraw_fn=lambda ch: str(ch.get("withdrawable", "")).lower() == "true",
            deposit_fn=lambda ch: str(ch.get("rechargeable", "")).lower() == "true",
        )
    return result


_FETCHERS = {"gateio": _fetch_gateio, "bitget": _fetch_bitget}


def _get_exchange_map(exchange: str, *, force: bool = False) -> dict[str, list[dict[str, Any]]]:
    """Return cached (or freshly fetched) coin->chains map for one exchange."""
    now = time.time()
    with _lock:
        cached = _cache.get(exchange)
        if not force and cached and (now - cached[0]) < _CACHE_TTL_SEC:
            return cached[1]
    # Fetch outside the lock to avoid blocking other exchanges.
    try:
        data = _FETCHERS[exchange]()
    except Exception as e:  # noqa: BLE001 — network errors are expected, degrade gracefully
        logger.warning("coin_networks: %s fetch failed: %s", exchange, e)
        with _lock:
            stale = _cache.get(exchange)
        # Serve stale data if we have it; otherwise an empty map.
        return stale[1] if stale else {}
    with _lock:
        _cache[exchange] = (now, data)
    return data


def get_coin_networks(
    coins: list[str],
    exchanges: tuple[str, ...] = SUPPORTED_EXCHANGES,
    *,
    force: bool = False,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Return {COIN: {exchange: [{network, raw, deposit, withdraw}]}}.

    Only requested coins that exist on an exchange appear in its sub-map.
    Unsupported exchanges are simply absent (never fabricated).
    """
    wanted = {c.strip().upper() for c in coins if c and c.strip()}
    ex_maps = {
        ex: _get_exchange_map(ex, force=force)
        for ex in exchanges
        if ex in _FETCHERS
    }
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for coin in wanted:
        per_ex: dict[str, list[dict[str, Any]]] = {}
        for ex, m in ex_maps.items():
            chains = m.get(coin)
            if chains:
                per_ex[ex] = chains
        if per_ex:
            out[coin] = per_ex
    return out
