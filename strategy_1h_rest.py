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

def get_bars(symbols, days=10):
    """Hourly bars for all symbols in one call (Alpaca market data, IEX feed).
    Returns {symbol: [close, ...]} oldest -> newest. Missing symbols are omitted."""
    out = {}
    start = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"APCA-API-KEY-ID": cfg("ALPACA_KEY"), "APCA-API-SECRET-KEY": cfg("ALPACA_SECRET")}
    token = None
    for _ in range(5):
        params = {"symbols": ",".join(symbols), "timeframe": "1Hour", "start": start,
                  "limit": 1000, "feed": "iex", "adjustment": "raw", "sort": "asc"}
        if token:
            params["page_token"] = token
        try:
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=headers,
                             params=params, timeout=20)
            if r.status_code != 200:
                print(f"Bars error {r.status_code}: {r.text[:120]}")
                break
            j = r.json()
        except Exception as e:
            print(f"Bars error: {e}")
            break
        for s, bars in (j.get("bars") or {}).items():
            out.setdefault(s, []).extend(b["c"] for b in bars)
        token = j.get("next_page_token")
        if not token:
            break
    return out

# Strategy parameters (documented in README)
LEVEL_PCTS = [0.20, 0.30, 0.50]          # share of the per-symbol budget at each level
DIP_L2, DIP_L3 = 0.008, 0.015            # add at -0.8% and -1.5% below the level-1 price
TAKE_PROFIT = 0.025                      # +2.5% above average entry
TRAIL_ARM, TRAIL_PCT = 0.01, 0.01        # trailing stop of 1% from peak, armed after +1% gain
HARD_STOP = 0.03                         # protective stop 3% below average entry

def decide(pos, price, regime):
    """Pure decision function. pos = dict(qty, avg, level, l1, peak) or {}.
    Returns ('buy', level) | ('sell', reason) | (None, None)."""
    qty = pos.get('qty', 0)
    if qty <= 0:
        return ('buy', 1) if regime == Regime.BULL else (None, None)
    avg, level, l1 = pos['avg'], pos.get('level', 1), pos.get('l1', pos['avg'])
    peak = max(pos.get('peak', avg), price)
    if price >= avg * (1 + TAKE_PROFIT):
        return 'sell', 'TP'
    if price <= avg * (1 - HARD_STOP):
        return 'sell', 'STOP'
    if peak >= avg * (1 + TRAIL_ARM) and price <= peak * (1 - TRAIL_PCT):
        return 'sell', 'TRAIL'
    if level == 1 and price <= l1 * (1 - DIP_L2):
        return 'buy', 2
    if level == 2 and price <= l1 * (1 - DIP_L3):
        return 'buy', 3
    return None, None

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

def main():
    print("\n" + "=" * 70)
    print("🚀 1-HOUR INTRADAY STRATEGY - LIVE EXECUTION (paper)")
    print("=" * 70)
    print(f"Time: {dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    dry = os.environ.get("DRY_RUN") == "1"

    alpaca = AlpacaAPI()
    acct = alpaca.get_account()
    if not acct:
        print("❌ Failed to connect to Alpaca")
        raise SystemExit(1)
    print("✅ Connected to Alpaca")
    print(f"   Account: ${float(acct.get('equity', 0)):,.0f} | Cash: ${float(acct.get('cash', 0)):,.0f}")

    clock = alpaca.get("/clock")
    if clock is not None and not clock.get("is_open", True):
        print("⏸ Market closed - no trading this cycle")
        return

    symbols = []
    if os.path.exists("portfolio_state.csv"):
        df = pd.read_csv("portfolio_state.csv")
        if not df.empty:
            symbols += [s.strip() for s in str(df.iloc[-1].get('picks', '')).split(';') if s.strip()]
    if os.path.exists("ai_basket.csv"):
        df = pd.read_csv("ai_basket.csv")
        if not df.empty:
            symbols += df['ticker'].unique().tolist()
    symbols = [s for s in dict.fromkeys(symbols) if '.' not in s][:12]   # keep order, US tickers only
    if not symbols:
        symbols = ["NVDA", "PLTR", "AAPL", "MSFT"]
    print(f"Monitoring: {', '.join(symbols)}\n")

    state_file = "strategy_1h_live_state.json"
    state = json.load(open(state_file)) if os.path.exists(state_file) else {}

    # Reconcile with the real Alpaca positions (source of truth)
    held = {p['symbol']: p for p in alpaca.get_positions()}
    for s in symbols:
        if s in held:
            st = state.get(s, {})
            st.update(qty=int(float(held[s]['qty'])), avg=float(held[s]['avg_entry_price']))
            st.setdefault('level', 1)
            st.setdefault('l1', st['avg'])
            state[s] = st
        else:
            state.pop(s, None)

    bars = get_bars(symbols)
    equity = float(acct.get('equity', 100000))
    budget = (equity * 0.5) / len(symbols)
    signals = []

    for sym in symbols:
        closes = bars.get(sym, [])
        price = get_price(sym) or (closes[-1] if closes else None)
        if not price or len(closes) < 10:
            print(f"⚠️  {sym}: insufficient data ({len(closes)} bars) - skipped")
            continue
        regime = detect_regime(np.array(closes, dtype=float))
        pos = state.get(sym, {})
        action, what = decide(pos, price, regime)
        line = f"{sym}: ${price:.2f} | {regime.value}"

        if pos:
            pos['peak'] = max(pos.get('peak', pos['avg']), price)

        if action == 'buy':
            level = what
            qty = int((budget * LEVEL_PCTS[level - 1]) / price)
            if qty >= 1:
                ok = dry or alpaca.place_order(sym, qty, "buy")
                if ok:
                    if level == 1:
                        state[sym] = {'qty': qty, 'avg': price, 'level': 1, 'l1': price, 'peak': price}
                    else:
                        tot = pos['qty'] + qty
                        pos.update(avg=(pos['avg'] * pos['qty'] + price * qty) / tot, qty=tot, level=level)
                    signals.append(f"✅ BUY_L{level} {sym}: {qty} @ ${price:.2f}")
                    line += f" | BUY_L{level} x{qty}"
                else:
                    line += " | ❌ order failed"
        elif action == 'sell':
            qty, avg = pos['qty'], pos['avg']
            ok = dry or alpaca.place_order(sym, qty, "sell")
            if ok:
                pnl_pct = (price / avg - 1) * 100
                icon = {'TP': '✅', 'TRAIL': '🔔', 'STOP': '🛑'}[what]
                signals.append(f"{icon} EXIT_{what} {sym}: {qty} @ ${price:.2f} | PnL: {pnl_pct:+.2f}%")
                line += f" | EXIT_{what} {pnl_pct:+.2f}%"
                state.pop(sym, None)
            else:
                line += " | ❌ order failed"
        print("  " + line)

    json.dump(state, open(state_file, 'w'), indent=2)
    print("\n" + "=" * 70)
    print(f"✅ Cycle completed: {len(signals)} signals | positions tracked: {len(state)}" + (" (DRY RUN)" if dry else ""))
    if signals and not dry:
        msg = "🟢 <b>1H Strategy Signals</b>\n\n" + "\n".join(signals[:10])
        print("✅ Telegram notification sent" if send_telegram(msg) else "⚠️  Telegram notification failed")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
