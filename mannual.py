import time
import hmac
import base64
import hashlib
import json
import logging
from typing import Any, Dict, Optional

import requests
import streamlit as st

LOGGER = logging.getLogger(__name__)
REST_BASE = "https://api.bitget.com"

def ms_timestamp() -> str:
    return str(int(time.time() * 1000))

def sign(secret: str, prehash: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")

def request(method: str, path: str, *,
           params: Optional[Dict[str, Any]] = None,
           body: Optional[Dict[str, Any]] = None,
           api_key: str = "",
           api_secret: str = "",
           api_passphrase: str = "",
           auth: bool = False,
           timeout: float = 10.0) -> Dict[str, Any]:
    params = params or {}
    body = body or {}
    url = f"{REST_BASE}{path}"

    # requestPath for signing includes query string
    request_path = path
    if params:
        from urllib.parse import urlencode
        query = urlencode(params, doseq=True)
        request_path = f"{path}?{query}"

    headers = {"Content-Type": "application/json"}
    body_str = json.dumps(body) if body and method.upper() != "GET" else ""

    if auth:
        ts = ms_timestamp()
        prehash = f"{ts}{method.upper()}{request_path}{body_str}"
        signature = sign(api_secret, prehash)
        headers.update({
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": api_passphrase,
        })

    try:
        if method.upper() == "GET":
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        elif method.upper() == "POST":
            resp = requests.post(url, params=params, data=body_str, headers=headers, timeout=timeout)
        elif method.upper() == "DELETE":
            resp = requests.delete(url, params=params, data=body_str, headers=headers, timeout=timeout)
        else:
            raise ValueError(f"Unsupported method: {method}")

        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        if resp.status_code >= 400:
            err_code = data.get("code") if isinstance(data, dict) else None
            err_msg = (isinstance(data, dict) and (data.get("msg") or data.get("message"))) or None
            raise RuntimeError(f"HTTP {resp.status_code} {url} code={err_code} msg={err_msg} body={data or resp.text}")

        return data
    except requests.RequestException as exc:
        raise RuntimeError(f"HTTP error {method} {url}: {exc}") from exc

def derive_mix_params(symbol_perp: str) -> Dict[str, str]:
    core = symbol_perp
    product_type = "USDT-FUTURES"
    if "_" in symbol_perp:
        parts = symbol_perp.split("_", 1)
        core = parts[0] or symbol_perp
        suf = parts[1].upper() if len(parts) > 1 else ""
        if suf.startswith("UMC"):
            product_type = "USDT-FUTURES"
        elif suf.startswith("CMC") or suf.startswith("DMC"):
            product_type = "COIN-FUTURES"
    return {"symbol": core, "productType": product_type}

def get_funding_rate(symbol_perp: str) -> float:
    params_v2 = derive_mix_params(symbol_perp)
    try:
        d = request("GET", "/api/v2/mix/market/current-fund-rate", params=params_v2)
    except RuntimeError:
        d = request("GET", "/api/mix/v1/market/funding-rate", params={"symbol": symbol_perp})
    payload = d.get("data")
    if isinstance(payload, dict) and "fundingRate" in payload:
        return float(payload["fundingRate"])
    if isinstance(payload, list) and payload and "fundingRate" in payload[0]:
        return float(payload[0]["fundingRate"])
    raise RuntimeError(f"Unexpected funding rate response: {d}")

def get_mark_price(symbol_perp: str) -> float:
    params_v2 = derive_mix_params(symbol_perp)
    for path, params in [
        ("/api/v2/mix/market/mark-price", params_v2),
        ("/api/v2/mix/market/ticker", params_v2),
        ("/api/mix/v1/market/mark-price", {"symbol": symbol_perp}),
        ("/api/mix/v1/market/ticker", {"symbol": symbol_perp}),
    ]:
        try:
            d = request("GET", path, params=params)
            payload = d.get("data")
            if isinstance(payload, dict):
                mp = payload.get("markPrice") or payload.get("close") or payload.get("last")
                if mp is not None:
                    return float(mp)
            if isinstance(payload, list) and payload:
                mp = payload[0].get("markPrice") or payload[0].get("close") or payload[0].get("last")
                if mp is not None:
                    return float(mp)
        except RuntimeError:
            continue
    raise RuntimeError("Mark price fetch failed across endpoints")

def place_spot_order(api_key, api_secret, api_passphrase,
                     symbol_spot: str, side: str, order_type: str, size: str,
                     quote_usdt: Optional[str] = None) -> Dict[str, Any]:
    # v2 prefers plain symbol, v1 often needs _SPBL
    symbol_v2 = symbol_spot.split("_", 1)[0]
    symbol_v1 = symbol_spot if "_" in symbol_spot else f"{symbol_spot}_SPBL"

    body_v2 = {
        "symbol": symbol_v2,
        "side": side,
        "orderType": order_type,
        "force": "gtc",
        "size": size,
    }
    if order_type.lower() == "market" and side.lower() == "buy" and quote_usdt:
        body_v2["quoteOrderQty"] = quote_usdt

    endpoints = [
        ("/api/v2/spot/trade/place-order", body_v2),
        ("/api/spot/v1/trade/place-order", {"symbol": symbol_v1, "side": side, "orderType": order_type, "size": size, "force": "gtc"}),
        ("/api/spot/v1/trade/orders", {"symbol": symbol_v1, "side": side, "orderType": order_type, "quantity": size, "force": "gtc"}),
    ]
    last_err = None
    for path, body in endpoints:
        try:
            return request("POST", path, body=body, auth=True,
                           api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("Failed to place spot order")

def place_mix_order(api_key, api_secret, api_passphrase,
                    symbol_perp: str, margin_coin: str,
                    side: str, order_type: str, size: str) -> Dict[str, Any]:
    body_v2 = {
        "symbol": symbol_perp,
        "marginCoin": margin_coin,
        "side": side,
        "orderType": order_type,
        "size": size,
        "timeInForceValue": "normal",
    }
    try:
        return request("POST", "/api/v2/mix/order/place-order", body=body_v2, auth=True,
                       api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
    except Exception:
        return request("POST", "/api/mix/v1/order/place-order", body=body_v2, auth=True,
                       api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)

def get_spot_assets(api_key, api_secret, api_passphrase) -> Dict[str, Any]:
    return request("GET", "/api/spot/v1/account/assets", auth=True,
                   api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)

def get_mix_accounts(api_key, api_secret, api_passphrase) -> Dict[str, Any]:
    return request("GET", "/api/mix/v1/account/accounts", params={"productType": "umcbl"}, auth=True,
                   api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)

def run_check_and_trade(api_key, api_secret, api_passphrase,
                        symbol_spot: str, symbol_perp: str,
                        funding_threshold: float, usd_notional: float, margin_coin: str = "USDT") -> str:
    funding = get_funding_rate(symbol_perp)
    if funding < funding_threshold:
        return f"Funding {funding:.6f} < threshold {funding_threshold:.6f}: no trade"

    mark = get_mark_price(symbol_perp)
    qty_base = max(usd_notional / mark, 0.00001)
    qty_str = f"{qty_base:.6f}"

    spot = place_spot_order(api_key, api_secret, api_passphrase,
                            symbol_spot=symbol_spot, side="buy", order_type="market",
                            size=qty_str, quote_usdt=f"{usd_notional:.2f}")
    perp = place_mix_order(api_key, api_secret, api_passphrase,
                           symbol_perp=symbol_perp, margin_coin=margin_coin,
                           side="open_short", order_type="market", size=qty_str)
    return f"Opened: spot={spot} perp={perp}"

def main():
    st.set_page_config(page_title="Bitget Funding Arb Bot (Single-file)", layout="wide")
    st.title("Bitget Funding Rate Arbitrage Bot")

    # Secrets: set in Streamlit Cloud -> App settings -> Secrets
    # BITGET_API_KEY, BITGET_API_SECRET, BITGET_API_PASSPHRASE
    api_key = st.secrets.get("BITGET_API_KEY", "")
    api_secret = st.secrets.get("BITGET_API_SECRET", "")
    api_passphrase = st.secrets.get("BITGET_API_PASSPHRASE", "")

    with st.sidebar:
        st.header("Parameters")
        symbol_spot = st.text_input("Spot symbol", value="BTCUSDT")
        symbol_perp = st.text_input("Perp symbol", value="BTCUSDT_UMCBL")
        funding_threshold = st.number_input("Funding threshold", value=0.0001, format="%.6f")
        usd_notional = st.number_input("USD notional", min_value=1.0, value=10.0, step=1.0)
        st.caption("Set your API keys in Streamlit secrets.")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("Market")
        if st.button("Fetch Funding & Mark"):
            try:
                f = get_funding_rate(symbol_perp)
                m = get_mark_price(symbol_perp)
                st.success(f"Funding: {f:.6f}\nMark: {m}")
            except Exception as e:
                st.error(str(e))

        if st.button("Get Balances"):
            try:
                spot_bal = get_spot_assets(api_key, api_secret, api_passphrase)
                perp_bal = get_mix_accounts(api_key, api_secret, api_passphrase)
                st.write({"spot": spot_bal, "perp": perp_bal})
            except Exception as e:
                st.error(str(e))

    with col2:
        st.subheader("Actions")
        if st.button("Check & Trade Once"):
            try:
                msg = run_check_and_trade(api_key, api_secret, api_passphrase,
                                          symbol_spot, symbol_perp, funding_threshold, usd_notional)
                st.success(msg)
            except Exception as e:
                st.error(str(e))

    with col3:
        st.subheader("Notes")
        st.write("- Market spot buys use quote USDT; ensure min notional and balances.")
        st.write("- Perp is USDT-margined; maintain margin buffer.")
        st.write("- Funding must be ≥ threshold to open.")

if __name__ == "__main__":
    main()
