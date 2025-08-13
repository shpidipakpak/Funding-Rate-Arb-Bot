import json
import logging
import os
import time

from bitget_api import BitgetAPI
from strategy import FundingArbStrategy


def setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    setup_logging()

    # Load credentials from environment first, then optional JSON fallback
    api_key = os.environ.get("BITGET_API_KEY")
    api_secret = os.environ.get("BITGET_API_SECRET")
    api_passphrase = os.environ.get("BITGET_API_PASSPHRASE")

    if not (api_key and api_secret and api_passphrase):
        try:
            with open("bitget_keys.json", "r", encoding="utf-8") as f:
                keys = json.load(f)
            api_key = api_key or keys.get("api_key")
            api_secret = api_secret or keys.get("api_secret")
            api_passphrase = api_passphrase or keys.get("api_passphrase")
        except FileNotFoundError:
            pass

    api = BitgetAPI(
        api_key=api_key or "",
        api_secret=api_secret or "",
        api_passphrase=api_passphrase or "",
    )

    # Default symbols (can be overridden via env vars)
    symbol_spot = os.environ.get("SYMBOL_SPOT", "BTCUSDT")
    symbol_perp = os.environ.get("SYMBOL_PERP", "BTCUSDT_UMCBL")
    funding_threshold = float(os.environ.get("FUNDING_THRESHOLD", "0.0001"))  # 0.01%
    usd_notional = float(os.environ.get("USD_NOTIONAL", "50"))

    strat = FundingArbStrategy(
        api=api,
        symbol_spot=symbol_spot,
        symbol_perp=symbol_perp,
        funding_threshold=funding_threshold,
        margin_coin="USDT",
        target_usd_notional=usd_notional,
    )

    logging.getLogger(__name__).info(
        "Starting loop: spot=%s perp=%s threshold=%.6f usd=%.2f",
        symbol_spot,
        symbol_perp,
        funding_threshold,
        usd_notional,
    )

    while True:
        try:
            strat.check_and_trade()
        except Exception:
            logging.getLogger(__name__).exception("Iteration failed")
        time.sleep(30)


if __name__ == "__main__":
    main()


