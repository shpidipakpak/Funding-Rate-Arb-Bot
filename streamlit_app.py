import json
import os
import sys
from pathlib import Path
from typing import Optional

import streamlit as st

# Make sure local modules are importable whether the app is at repo root or in a subfolder
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR
if (APP_DIR / "bitget_api.py").exists() and (APP_DIR / "strategy.py").exists():
    REPO_ROOT = APP_DIR
elif (APP_DIR.parent / "bitget_api.py").exists() and (APP_DIR.parent / "strategy.py").exists():
    REPO_ROOT = APP_DIR.parent
for p in {str(REPO_ROOT), str(REPO_ROOT.parent)}:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from bitget_api import BitgetAPI
    from strategy import FundingArbStrategy
except Exception:
    # Fallback: inline minimal implementations so the app can run standalone on Streamlit Cloud
    import time
    import hmac
    import base64
    import hashlib
    from typing import Any, Dict
    import requests

    class BitgetAPI:  # minimal inline
        REST_BASE = "https://api.bitget.com"

        def __init__(self, api_key: str = "", api_secret: str = "", api_passphrase: str = "") -> None:
            self.api_key = api_key
            self.api_secret = api_secret
            self.api_passphrase = api_passphrase
            self._session = requests.Session()

        @staticmethod
        def _ms_timestamp() -> str:
            return str(int(time.time() * 1000))

        def _sign(self, prehash: str) -> str:
            mac = hmac.new(self.api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
            return base64.b64encode(mac.digest()).decode("utf-8")

        def _request(self, method: str, path: str, *, params: Dict[str, Any] | None = None, body: Dict[str, Any] | None = None, auth: bool = False, timeout: float = 10.0) -> Dict[str, Any]:
            params = params or {}
            body = body or {}
            url = f"{self.REST_BASE}{path}"
            request_path = path
            if params:
                from urllib.parse import urlencode
                request_path = f"{path}?{urlencode(params, doseq=True)}"
            headers = {"Content-Type": "application/json"}
            body_str = json.dumps(body) if body and method.upper() != "GET" else ""
            if auth:
                ts = self._ms_timestamp()
                prehash = f"{ts}{method.upper()}{request_path}{body_str}"
                sig = self._sign(prehash)
                headers.update({
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                })
            if method.upper() == "GET":
                resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
            elif method.upper() == "POST":
                resp = self._session.post(url, params=params, data=body_str, headers=headers, timeout=timeout)
            else:
                raise ValueError("Unsupported method")
            data: Dict[str, Any] = {}
            try:
                data = resp.json()
            except Exception:
                pass
            if resp.status_code >= 400:
                code = data.get("code") if isinstance(data, dict) else None
                msg = (isinstance(data, dict) and (data.get("msg") or data.get("message"))) or None
                raise RuntimeError(f"HTTP {resp.status_code} {url} code={code} msg={msg} body={data or resp.text}")
            return data

        @staticmethod
        def _derive_mix_params(symbol_perp: str) -> Dict[str, str]:
            core = symbol_perp
            product_type = "USDT-FUTURES"
            if "_" in symbol_perp:
                p = symbol_perp.split("_", 1)
                core = p[0] or symbol_perp
                suf = p[1].upper() if len(p) > 1 else ""
                if suf.startswith("UMC"):
                    product_type = "USDT-FUTURES"
                elif suf.startswith("CMC") or suf.startswith("DMC"):
                    product_type = "COIN-FUTURES"
            return {"symbol": core, "productType": product_type}

        def get_funding_rate(self, symbol_perp: str) -> Dict[str, Any]:
            params_v2 = self._derive_mix_params(symbol_perp)
            try:
                return self._request("GET", "/api/v2/mix/market/current-fund-rate", params=params_v2)
            except Exception:
                return self._request("GET", "/api/mix/v1/market/funding-rate", params={"symbol": symbol_perp})

        def get_mark_price(self, symbol_perp: str) -> Dict[str, Any]:
            params_v2 = self._derive_mix_params(symbol_perp)
            for path, params in [
                ("/api/v2/mix/market/mark-price", params_v2),
                ("/api/v2/mix/market/ticker", params_v2),
                ("/api/mix/v1/market/mark-price", {"symbol": symbol_perp}),
                ("/api/mix/v1/market/ticker", {"symbol": symbol_perp}),
            ]:
                try:
                    return self._request("GET", path, params=params)
                except Exception:
                    continue
            raise RuntimeError("Mark price fetch failed")

        def get_spot_assets(self) -> Dict[str, Any]:
            return self._request("GET", "/api/spot/v1/account/assets", auth=True)

        def get_mix_accounts(self) -> Dict[str, Any]:
            return self._request("GET", "/api/mix/v1/account/accounts", params={"productType": "umcbl"}, auth=True)

        def place_spot_order(self, symbol_spot: str, side: str, quantity: str, order_type: str = "market", price: str | None = None, quote_amount: str | None = None) -> Dict[str, Any]:
            symbol_v2 = symbol_spot.split("_", 1)[0]
            symbol_v1 = symbol_spot if "_" in symbol_spot else f"{symbol_spot}_SPBL"
            body_v2 = {"symbol": symbol_v2, "side": side, "orderType": order_type, "force": "gtc", "size": quantity}
            if order_type.lower() == "market" and side.lower() == "buy" and quote_amount:
                body_v2["quoteOrderQty"] = quote_amount
            endpoints = [
                ("/api/v2/spot/trade/place-order", body_v2),
                ("/api/spot/v1/trade/place-order", {"symbol": symbol_v1, "side": side, "orderType": order_type, "size": quantity, "force": "gtc"}),
                ("/api/spot/v1/trade/orders", {"symbol": symbol_v1, "side": side, "orderType": order_type, "quantity": quantity, "force": "gtc"}),
            ]
            last = None
            for path, body in endpoints:
                try:
                    return self._request("POST", path, body=body, auth=True)
                except Exception as e:
                    last = e
                    continue
            raise last or RuntimeError("spot place failed")

        def place_mix_order(self, symbol_perp: str, margin_coin: str, side: str, size: str, order_type: str = "market") -> Dict[str, Any]:
            body = {"symbol": symbol_perp, "marginCoin": margin_coin, "side": side, "orderType": order_type, "size": size, "timeInForceValue": "normal"}
            try:
                return self._request("POST", "/api/v2/mix/order/place-order", body=body, auth=True)
            except Exception:
                return self._request("POST", "/api/mix/v1/order/place-order", body=body, auth=True)

    class FundingArbStrategy:  # minimal inline
        def __init__(self, api: BitgetAPI, symbol_spot: str, symbol_perp: str, funding_threshold: float, margin_coin: str, target_usd_notional: float) -> None:
            self.api = api
            self.symbol_spot = symbol_spot
            self.symbol_perp = symbol_perp
            self.funding_threshold = funding_threshold
            self.margin_coin = margin_coin
            self.target_usd_notional = target_usd_notional

        def _get_current_funding(self) -> float:
            d = self.api.get_funding_rate(self.symbol_perp)
            payload = d.get("data") if isinstance(d, dict) else None
            if isinstance(payload, dict) and "fundingRate" in payload:
                return float(payload["fundingRate"])
            if isinstance(payload, list) and payload and "fundingRate" in payload[0]:
                return float(payload[0]["fundingRate"])
            raise RuntimeError(f"Unexpected funding response: {d}")

        def _get_mark_price(self) -> float:
            d = self.api.get_mark_price(self.symbol_perp)
            payload = d.get("data") if isinstance(d, dict) else None
            if isinstance(payload, dict):
                mp = payload.get("markPrice") or payload.get("close") or payload.get("last")
                if mp is not None:
                    return float(mp)
            if isinstance(payload, list) and payload:
                mp = payload[0].get("markPrice") or payload[0].get("close") or payload[0].get("last")
                if mp is not None:
                    return float(mp)
            raise RuntimeError(f"Unexpected mark response: {d}")

        def check_and_trade(self) -> None:
            f = self._get_current_funding()
            if f < self.funding_threshold:
                return
            self._open_pair_trade()

        def _open_pair_trade(self) -> None:
            mark = self._get_mark_price()
            qty = max(self.target_usd_notional / mark, 0.00001)
            qty_str = f"{qty:.6f}"
            self.api.place_spot_order(self.symbol_spot, side="buy", quantity=qty_str, order_type="market", quote_amount=f"{self.target_usd_notional:.2f}")
            self.api.place_mix_order(self.symbol_perp, margin_coin=self.margin_coin, side="open_short", size=qty_str, order_type="market")

        def _close_pair_trade(self) -> None:
            # Close both legs using market
            # Note: For spot, we sell the same qty; for perp, we buy to close short
            # In production you'd fetch exact position sizes
            mark = self._get_mark_price()
            qty = max(self.target_usd_notional / mark, 0.00001)
            qty_str = f"{qty:.6f}"
            self.api.place_spot_order(self.symbol_spot, side="sell", quantity=qty_str, order_type="market")
            self.api.place_mix_order(self.symbol_perp, margin_coin=self.margin_coin, side="close_short", size=qty_str, order_type="market")


STATE_FILE = "state.json"
KEYS_FILE = "bitget_keys.json"


def load_local_keys() -> tuple[Optional[str], Optional[str], Optional[str]]:
    api_key = os.environ.get("BITGET_API_KEY")
    api_secret = os.environ.get("BITGET_API_SECRET")
    api_passphrase = os.environ.get("BITGET_API_PASSPHRASE")
    if api_key and api_secret and api_passphrase:
        return api_key, api_secret, api_passphrase
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            keys = json.load(f)
        return keys.get("api_key"), keys.get("api_secret"), keys.get("api_passphrase")
    except FileNotFoundError:
        return None, None, None


def get_api(api_key: str, api_secret: str, api_passphrase: str) -> BitgetAPI:
    return BitgetAPI(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)


def read_state() -> dict:
    if Path(STATE_FILE).exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def main() -> None:
    st.set_page_config(page_title="Bitget Funding Arb Bot", layout="wide")
    st.title("Bitget Funding Rate Arbitrage Bot")

    # Sidebar: credentials and parameters
    with st.sidebar:
        st.header("Credentials")
        local_key, local_secret, local_pass = load_local_keys()
        api_key = st.text_input("API Key", value=local_key or "", type="password")
        api_secret = st.text_input("API Secret", value=local_secret or "", type="password")
        api_passphrase = st.text_input("API Passphrase", value=local_pass or "", type="password")

        st.header("Parameters")
        symbol_spot = st.text_input("Spot symbol", value=os.environ.get("SYMBOL_SPOT", "BTCUSDT"))
        symbol_perp = st.text_input("Perp symbol", value=os.environ.get("SYMBOL_PERP", "BTCUSDT_UMCBL"))
        funding_threshold = st.number_input("Funding threshold", value=float(os.environ.get("FUNDING_THRESHOLD", "0.0001")), format="%.6f")
        usd_notional = st.number_input("USD notional", min_value=1.0, value=float(os.environ.get("USD_NOTIONAL", "50")), step=1.0)

        st.caption("Tip: Save secrets in bitget_keys.json or env vars. They are not stored by this app.")

    # Instantiate API and Strategy
    api = get_api(api_key, api_secret, api_passphrase)
    strategy = FundingArbStrategy(
        api=api,
        symbol_spot=symbol_spot,
        symbol_perp=symbol_perp,
        funding_threshold=funding_threshold,
        margin_coin="USDT",
        target_usd_notional=float(usd_notional),
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("Market Data")
        if st.button("Fetch Funding & Mark"):
            try:
                funding = strategy._get_current_funding()
                mark = strategy._get_mark_price()
                st.success(f"Funding: {funding:.6f}\nMark: {mark}")
            except Exception as e:
                st.error(f"Error: {e}")

        if st.button("Get Balances"):
            try:
                spot_bal = api.get_spot_assets()
                perp_bal = api.get_mix_accounts()
                st.write({"spot": spot_bal, "perp": perp_bal})
            except Exception as e:
                st.error(f"Error: {e}")

    with col2:
        st.subheader("Actions")
        st.caption("One-click actions. Use carefully on live keys.")
        if st.button("Check & Trade Once"):
            try:
                strategy.check_and_trade()
                st.success("check_and_trade executed")
            except Exception as e:
                st.error(f"Error: {e}")

        if st.button("Open Pair Now"):
            try:
                strategy._open_pair_trade()
                st.success("Opened pair")
            except Exception as e:
                st.error(f"Error: {e}")

        if st.button("Close Pair Now"):
            try:
                strategy._close_pair_trade()
                st.success("Closed pair")
            except Exception as e:
                st.error(f"Error: {e}")

    with col3:
        st.subheader("State & Logs")
        state = read_state()
        st.write(state)
        st.caption("State is stored in state.json.")

    st.markdown("---")
    st.subheader("Notes")
    st.markdown(
        "- For market spot buys, the bot uses quote notional (USDT). Ensure min notional and balances.\n"
        "- Perp orders are USDT-margined. Maintain sufficient margin.\n"
        "- Use the threshold to control when trades trigger."
    )


if __name__ == "__main__":
    main()


