import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional

from bitget_api import BitgetAPI


LOGGER = logging.getLogger(__name__)


@dataclass
class PositionState:
    symbol_spot: str
    symbol_perp: str
    usd_notional: float
    spot_qty: float
    perp_size: float
    entry_mark_price: float
    opened_ts: float
    closed_ts: Optional[float] = None


class FundingArbStrategy:
    """
    Minimal funding arbitrage logic:
    - If funding > threshold: long spot, short perp for equal USD notional
    - Hold until after funding payout, then close both
    - If funding drops below threshold before payout, close early

    Storage: JSON file `state.json` for persistence across restarts.
    """

    def __init__(
        self,
        api: BitgetAPI,
        symbol_spot: str,
        symbol_perp: str,
        funding_threshold: float = 0.0001,  # 0.01%
        margin_coin: str = "USDT",
        state_path: str = "state.json",
        target_usd_notional: float = 50.0,
    ) -> None:
        self.api = api
        self.symbol_spot = symbol_spot
        self.symbol_perp = symbol_perp
        self.margin_coin = margin_coin
        self.funding_threshold = funding_threshold
        self.state_path = state_path
        self.target_usd_notional = target_usd_notional
        self.position: Optional[PositionState] = None

        self._load_state()

    # ---------- Persistence ----------
    def _save_state(self) -> None:
        data: Dict[str, object] = {}
        if self.position is not None:
            data["position"] = asdict(self.position)
        else:
            data["position"] = None
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_state(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            pos = raw.get("position")
            if pos:
                self.position = PositionState(**pos)
            else:
                self.position = None
        except FileNotFoundError:
            self.position = None

    # ---------- Helpers ----------
    def _now(self) -> float:
        return time.time()

    def _get_current_funding(self) -> float:
        data = self.api.get_funding_rate(self.symbol_perp)
        # v2 may return {"data": {"fundingRate": "..."}},
        # v1 may return {"data": {"fundingRate": "..."}} or list forms.
        try:
            if isinstance(data, dict):
                d = data.get("data")
                if isinstance(d, dict) and "fundingRate" in d:
                    return float(d["fundingRate"])  # e.g., 0.0001 means 0.01%
                if isinstance(d, list) and d:
                    first = d[0]
                    if isinstance(first, dict) and "fundingRate" in first:
                        return float(first["fundingRate"])
        except Exception:
            LOGGER.exception("Failed to parse funding rate response: %s", data)
        raise RuntimeError(f"Unexpected funding rate response: {data}")

    def _get_mark_price(self) -> float:
        data = self.api.get_mark_price(self.symbol_perp)
        try:
            if isinstance(data, dict):
                d = data.get("data")
                if isinstance(d, dict):
                    mp = d.get("markPrice") or d.get("close") or d.get("last")
                    if mp is not None:
                        return float(mp)
                if isinstance(d, list) and d:
                    first = d[0]
                    if isinstance(first, dict):
                        mp = first.get("markPrice") or first.get("close") or first.get("last")
                        if mp is not None:
                            return float(mp)
        except Exception:
            LOGGER.exception("Failed to parse mark price response: %s", data)
        raise RuntimeError(f"Unexpected mark price response: {data}")

    # ---------- Core logic ----------
    def check_and_trade(self) -> None:
        funding = self._get_current_funding()
        LOGGER.info("Funding %s: %.6f", self.symbol_perp, funding)

        if self.position is None:
            if funding >= self.funding_threshold:
                self._open_pair_trade()
            return

        # If we have an open position, decide whether to close
        if funding < self.funding_threshold:
            LOGGER.info("Funding dropped below threshold; closing early")
            self._close_pair_trade()
            return

        # Basic time-based close: if more than 8 hours since open, close (Bitget often 8h periods)
        if self._now() - self.position.opened_ts > 8 * 3600:
            LOGGER.info("Funding window likely passed; closing position")
            self._close_pair_trade()

    def _open_pair_trade(self) -> None:
        mark_price = self._get_mark_price()
        usd = float(self.target_usd_notional)
        # Approx equal notional in spot base-qty
        spot_qty = max(usd / mark_price, 0.00001)
        qty_str = f"{spot_qty:.6f}"

        LOGGER.info(
            "Opening pair: +SPOT %s, -PERP %s, usd=%.2f, qty≈%s at mark %.2f",
            self.symbol_spot,
            self.symbol_perp,
            usd,
            qty_str,
            mark_price,
        )

        # Place spot buy (market). For market buys, pass quote amount in USDT to satisfy exchange min-notional rules
        spot = self.api.place_spot_order(
            symbol_spot=self.symbol_spot,
            side="buy",
            quantity=qty_str,
            order_type="market",
            quote_amount=f"{usd:.2f}",
        )
        LOGGER.info("Spot order response: %s", spot)

        # Place perp open short (market)
        perp = self.api.place_mix_order(
            symbol_perp=self.symbol_perp,
            margin_coin=self.margin_coin,
            side="open_short",
            size=qty_str,
            order_type="market",
        )
        LOGGER.info("Perp order response: %s", perp)

        self.position = PositionState(
            symbol_spot=self.symbol_spot,
            symbol_perp=self.symbol_perp,
            usd_notional=usd,
            spot_qty=float(qty_str),
            perp_size=float(qty_str),
            entry_mark_price=mark_price,
            opened_ts=self._now(),
        )
        self._save_state()

    def _close_pair_trade(self) -> None:
        if self.position is None:
            return

        qty_str = f"{self.position.spot_qty:.6f}"
        LOGGER.info("Closing pair for %s qty=%s", self.symbol_perp, qty_str)

        # Close spot (sell)
        spot = self.api.place_spot_order(
            symbol_spot=self.symbol_spot,
            side="sell",
            quantity=qty_str,
            order_type="market",
        )
        LOGGER.info("Spot close response: %s", spot)

        # Close perp (buy)
        perp = self.api.place_mix_order(
            symbol_perp=self.symbol_perp,
            margin_coin=self.margin_coin,
            side="close_short",
            size=qty_str,
            order_type="market",
        )
        LOGGER.info("Perp close response: %s", perp)

        self.position.closed_ts = self._now()
        self._save_state()
        LOGGER.info(
            "Closed pair. Held for %.1f min",
            (self.position.closed_ts - self.position.opened_ts) / 60.0,
        )
        self.position = None


