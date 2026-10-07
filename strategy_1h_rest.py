"""
1-Hour Intraday Strategy - Direct REST API (No SDK required)
Uses Alpaca REST API directly + Finnhub + Telegram
"""

import os
import json
import datetime as dt
from enum import Enum
import numpy as np
import requests
import pandas as pd

def cfg(name, default=None):
    """Load config"""
    v = os.environ.get(name)
    if v:
        return v.strip()
    try:
        for line in open("config.txt", encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default

# ============================================================================
# ALPACA REST API
# ============================================================================

class AlpacaAPI:
    def __init__(self):
        self.base_url = cfg("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2").rstrip("/")
        self.key = cfg("ALPACA_KEY")
        self.secret = cfg("ALPACA_SECRET")
        self.headers = {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret
        }

    def get(self, endpoint):
        """GET request"""
        try:
            resp = requests.get(f"{self.base_url}{endpoint}", headers=self.headers, timeout=10)
            return resp.json() if resp.status_code == 200 else None
        except Exception as e:
            print(f"Alpaca GET error: {e}")
            return None

    def post(self, endpoint, data):
        """POST request"""
        try:
            resp = requests.post(f"{self.base_url}{endpoint}", headers=self.headers, json=data, timeout=10)
            return resp.json() if resp.status_code in [200, 201] else None
        except Exception as e:
            print(f"Alpaca POST error: {e}")
            return None

    def get_account(self):
        """Get account info"""
        return self.get("/account")

    def get_positions(self):
        """Get open positions"""
        return self.get("/positions") or []

    def place_order(self, symbol, qty, side):
        """Place market order"""
        data = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": "market",
            "time_in_force": "day"
        }
        return self.post("/orders", data)

# ============================================================================
# REGIME DETECTION
# ============================================================================

class Regime(Enum):
    BULL = "🟢 BULL"
    BEAR = "🔴 BEAR"
    RANGE = "⚪ RANGE"

def detect_regime(prices, window=10):
    """Detect regime"""
    if len(prices) < window:
        return Regime.RANGE
    recent = prices[-window:]
    slope = (recent[-1] - recent[0]) / recent[0]
    volatility = np.std(np.diff(recent) / recent[:-1])

    if slope > 0.01 and volatility < 0.02:
        return Regime.BULL
    elif slope < -0.01 and volatility < 0.02:
        return Regime.BEAR
    else:
        return Regime.RANGE

def get_price(symbol):
    """Get price from Finnhub"""
    try:
        url = "https://finnhub.io/api/v1/quote"
        resp = requests.get(url, params={"symbol": symbol, "token": cfg("FINNHUB_KEY")}, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('c')
    except:
        pass
    return None

# ============================================================================
# TELEGRAM
# ============================================================================

def send_telegram(msg):
    """Send to Telegram"""
    try:
        token = cfg("TELEGRAM_BOT_TOKEN")
        chat = cfg("TELEGRAM_CHAT_ID")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat, "text": msg, "parse_mode": "HTML"}, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# ============================================================================
# MAIN STRATEGY
# ============================================================================

print("\n" + "=" * 70)
print("🚀 1-HOUR INTRADAY STRATEGY - LIVE EXECUTION")
print("=" * 70)
print(f"Time: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")

alpaca = AlpacaAPI()

# Verify connection
acct = alpaca.get_account()
if not acct:
    print("❌ Failed to connect to Alpaca")
    exit(1)

print(f"✅ Connected to Alpaca")
print(f"   Account: ${float(acct.get('equity', 0)):,.0f}")
print(f"   Cash: ${float(acct.get('cash', 0)):,.0f}")

# Load symbols
symbols = []
if os.path.exists("portfolio_state.csv"):
    df = pd.read_csv("portfolio_state.csv")
    if not df.empty:
        picks = df.iloc[-1].get('picks', '')
        symbols.extend([s.strip() for s in picks.split(';') if s.strip()])

if os.path.exists("ai_basket.csv"):
    df = pd.read_csv("ai_basket.csv")
    if not df.empty:
        symbols.extend(df['ticker'].unique().tolist())

symbols = list(set(symbols))[:12]

if not symbols:
    symbols = ["NVDA", "PLTR", "AAPL", "MSFT"]

print(f"Monitoring: {', '.join(symbols)}\n")

# Load state
state_file = "strategy_1h_live_state.json"
state = {}
if os.path.exists(state_file):
    with open(state_file) as f:
        state = json.load(f)

signals = []
equity = float(acct.get('equity', 100000))
cash_per_symbol = (equity * 0.5) / len(symbols)

for sym in symbols:
    price = get_price(sym)
    if not price:
        print(f"⚠️  {sym}: No price data")
        continue

    position = state.get(sym, {})
    qty = position.get('qty', 0)
    entry = position.get('entry_price', 0)
    level = position.get('entry_level', 0)

    # Simulate regime (in real scenario, fetch historical bars)
    regime = Regime.BULL if np.random.random() < 0.35 else Regime.RANGE

    signal = f"{sym}: ${price:.2f} | {regime.value}"

    # Entry: Level 1
    if qty == 0 and regime == Regime.BULL and np.random.random() < 0.15:
        trade_qty = max(1, int((cash_per_symbol * 0.2) / price))

        order = alpaca.place_order(sym, trade_qty, "buy")
        if order:
            state[sym] = {'qty': trade_qty, 'entry_price': price, 'entry_level': 1}
            signal += f" | ✅ BUY_L1: {trade_qty}"
            signals.append(f"✅ BUY_L1 {sym}: {trade_qty} @ ${price:.2f}")
            print(f"  {signal}")
        else:
            print(f"  ❌ {sym}: Order failed")

    # Exit: Stop or TP
    elif qty > 0:
        if price < entry * 0.99:
            pnl = (price - entry) * qty
            pnl_pct = (pnl / (entry * qty)) * 100

            order = alpaca.place_order(sym, qty, "sell")
            if order:
                signal += f" | 🛑 EXIT_STOP | PnL: {pnl_pct:+.2f}%"
                signals.append(f"🛑 EXIT_STOP {sym}: {qty} @ ${price:.2f} | PnL: {pnl_pct:+.2f}%")
                del state[sym]
                print(f"  {signal}")

        elif price > entry * 1.025:
            pnl = (price - entry) * qty
            pnl_pct = (pnl / (entry * qty)) * 100

            order = alpaca.place_order(sym, qty, "sell")
            if order:
                signal += f" | ✅ EXIT_TP | PnL: {pnl_pct:+.2f}%"
                signals.append(f"✅ EXIT_TP {sym}: {qty} @ ${price:.2f} | PnL: {pnl_pct:+.2f}%")
                del state[sym]
                print(f"  {signal}")

# Save state
with open(state_file, 'w') as f:
    json.dump(state, f, indent=2)

print("\n" + "=" * 70)
print(f"✅ Cycle completed: {len(signals)} signals")
print(f"📁 State saved | 📊 Positions: {len(state)}")

# Send Telegram
if signals:
    msg = "🟢 <b>1H Strategy Signals</b>\n\n"
    msg += "\n".join(signals[:10])
    if send_telegram(msg):
        print("✅ Telegram notification sent")
    else:
        print("⚠️  Telegram notification failed")

print("=" * 70 + "\n")
