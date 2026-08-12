"""Tests for cross-exchange coin transfer networks.

Covers:
- Network-name canonicalization across exchange-specific aliases.
- Gate.io / Bitget chain-list normalization (opposite flag conventions).
- get_coin_networks aggregation and filtering (cache monkeypatched, no network).
"""

from __future__ import annotations

import mexc_monitor.coin_networks as cn
from mexc_monitor.coin_networks import (
    canonical_network,
    get_coin_networks,
    _normalize_chain_list,
)


class TestCanonicalNetwork:
    def test_bsc_aliases_collapse_to_bep20(self):
        assert canonical_network("BSC") == "BEP20"
        assert canonical_network("BEP20") == "BEP20"
        assert canonical_network("BNB Smart Chain") == "BEP20"

    def test_eth_aliases_collapse_to_erc20(self):
        assert canonical_network("ETH") == "ERC20"
        assert canonical_network("ERC20") == "ERC20"
        assert canonical_network("Ethereum") == "ERC20"

    def test_tron_and_solana(self):
        assert canonical_network("TRX") == "TRC20"
        assert canonical_network("TRC20") == "TRC20"
        assert canonical_network("SOL") == "SOL"
        assert canonical_network("Solana") == "SOL"

    def test_case_and_punctuation_insensitive(self):
        assert canonical_network("avax-c") == "AVAX-C"
        assert canonical_network("Arbitrum One") == "ARBITRUM"

    def test_unknown_name_normalized_not_dropped(self):
        # Unknown chains keep an uppercased alnum-only form so they still
        # compare equal across exchanges that share the same raw name.
        assert canonical_network("weird-chain 7") == "WEIRDCHAIN7"

    def test_empty(self):
        assert canonical_network("") == ""


class TestNormalizeChainList:
    def test_gateio_disabled_flags_inverted(self):
        # Gate.io: *_disabled True == blocked.
        chains = [
            {"name": "BTC", "withdraw_disabled": False, "deposit_disabled": False},
            {"name": "ETH", "withdraw_disabled": True, "deposit_disabled": False},
        ]
        out = _normalize_chain_list(
            chains,
            name_key="name",
            withdraw_fn=lambda ch: not ch.get("withdraw_disabled", False),
            deposit_fn=lambda ch: not ch.get("deposit_disabled", False),
        )
        by_net = {c["network"]: c for c in out}
        assert by_net["BTC"]["withdraw"] is True
        assert by_net["BTC"]["deposit"] is True
        assert by_net["ERC20"]["withdraw"] is False  # ETH canonicalized
        assert by_net["ERC20"]["deposit"] is True

    def test_bitget_string_flags(self):
        # Bitget: positive string flags "true"/"false".
        chains = [
            {"chain": "BEP20", "withdrawable": "true", "rechargeable": "false"},
        ]
        out = _normalize_chain_list(
            chains,
            name_key="chain",
            withdraw_fn=lambda ch: str(ch.get("withdrawable", "")).lower() == "true",
            deposit_fn=lambda ch: str(ch.get("rechargeable", "")).lower() == "true",
        )
        assert out[0]["network"] == "BEP20"
        assert out[0]["withdraw"] is True
        assert out[0]["deposit"] is False

    def test_duplicate_canonical_networks_deduped(self):
        chains = [
            {"name": "BSC", "withdraw_disabled": False, "deposit_disabled": False},
            {"name": "BEP20", "withdraw_disabled": False, "deposit_disabled": False},
        ]
        out = _normalize_chain_list(
            chains,
            name_key="name",
            withdraw_fn=lambda ch: not ch.get("withdraw_disabled", False),
            deposit_fn=lambda ch: not ch.get("deposit_disabled", False),
        )
        assert [c["network"] for c in out] == ["BEP20"]


class TestGetCoinNetworks:
    def _seed_cache(self, monkeypatch):
        # Monkeypatch the per-exchange fetcher output via the cache getter so
        # no network calls happen.
        maps = {
            "gateio": {
                "BTC": [{"network": "BTC", "raw": "BTC", "withdraw": True, "deposit": True}],
                "SOL": [{"network": "SOL", "raw": "SOL", "withdraw": True, "deposit": True}],
            },
            "bitget": {
                "BTC": [{"network": "BTC", "raw": "BTC", "withdraw": True, "deposit": True}],
            },
        }
        monkeypatch.setattr(
            cn, "_get_exchange_map", lambda ex, *, force=False: maps.get(ex, {})
        )

    def test_aggregates_per_coin_per_exchange(self, monkeypatch):
        self._seed_cache(monkeypatch)
        out = get_coin_networks(["BTC", "SOL"])
        assert set(out["BTC"].keys()) == {"gateio", "bitget"}
        assert set(out["SOL"].keys()) == {"gateio"}  # only gate has SOL

    def test_case_insensitive_and_unknown_coin_absent(self, monkeypatch):
        self._seed_cache(monkeypatch)
        out = get_coin_networks(["btc", "DOESNOTEXIST"])
        assert "BTC" in out
        assert "DOESNOTEXIST" not in out

    def test_empty_input(self, monkeypatch):
        self._seed_cache(monkeypatch)
        assert get_coin_networks([]) == {}
