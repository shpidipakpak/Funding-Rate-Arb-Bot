## Perp Funding Arbitrage Demo

This repo contains a Jupyter notebook `perp_funding_arbitrage.ipynb` that showcases a funding-rate arbitrage prep workflow for crypto HFT/MM interviews.

Features:
- Live snapshot of Binance USDT-perp funding and implied APY
- Simple fee-aware backtest on recent funding history
- Capital-aware sizing and basic alerting

How to run:
1. Open the notebook in VS Code/Cursor or Jupyter
2. Run cells top-to-bottom. The notebook auto-installs minimal dependencies if missing

Notes:
- Uses Binance public endpoints only (no keys). For execution/hedging, wire in private API and risk controls
- Educational; not financial advice


## Bitget live bot (minimal)

Files:
- `bitget_api.py` – REST + WS helpers for Bitget
- `strategy.py` – simple funding-arb logic (spot long + perp short)
- `main.py` – run loop and config

Install:
```bash
pip install -r requirements.txt
```

Configure credentials (prefer environment variables):
- Windows (cmd):
```cmd
setx BITGET_API_KEY "<your_key>"
setx BITGET_API_SECRET "<your_secret>"
setx BITGET_API_PASSPHRASE "<your_passphrase>"
```
Open a new terminal after `setx`.

Alternatively, create a local file `bitget_keys.json` (not tracked by git):
```json
{
  "api_key": "<your_key>",
  "api_secret": "<your_secret>",
  "api_passphrase": "<your_passphrase>"
}
```

Run:
```bash
python main.py
```

Env overrides:
- `SYMBOL_SPOT` (default `BTCUSDT`)
- `SYMBOL_PERP` (default `BTCUSDT_UMCBL`)
- `FUNDING_THRESHOLD` (default `0.0001` i.e. 0.01%)
- `USD_NOTIONAL` (default `50`)


