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

from bitget_api import BitgetAPI
from strategy import FundingArbStrategy


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


