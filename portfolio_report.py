"""
Daily Portfolio Report - هر روز گزارش پرتفوی
Sends Telegram report with positions, PnL, and strategy status
"""

import os
import json
import datetime as dt
import requests
import pandas as pd
from enum import Enum

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
# ALPACA API
# ============================================================================

class AlpacaAPI:
    def __init__(self):
        self.base_url = cfg("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2").rstrip("/")
        self.headers = {
            "APCA-API-KEY-ID": cfg("ALPACA_KEY"),
            "APCA-API-SECRET-KEY": cfg("ALPACA_SECRET")
        }

    def get(self, endpoint):
        try:
            resp = requests.get(f"{self.base_url}{endpoint}", headers=self.headers, timeout=10)
            return resp.json() if resp.status_code == 200 else None
        except:
            return None

    def get_account(self):
        return self.get("/account")

    def get_positions(self):
        return self.get("/positions") or []

# ============================================================================
# PORTFOLIO ANALYSIS
# ============================================================================

def analyze_portfolio():
    """Analyze portfolio and return report data"""

    alpaca = AlpacaAPI()

    # Get account info
    acct = alpaca.get_account()
    if not acct:
        return None

    equity = float(acct.get('equity', 0))
    cash = float(acct.get('cash', 0))
    last_equity = float(acct.get('last_equity', equity) or equity)

    day_pnl = equity - last_equity
    day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0

    # Get positions
    positions = alpaca.get_positions()

    # Calculate stats
    total_position_value = 0
    winning_positions = 0
    losing_positions = 0
    total_unrealized_pnl = 0

    pos_list = []
    for p in positions:
        sym = p.get('symbol', '')
        qty = int(float(p.get('qty', 0)))
        value = float(p.get('market_value', 0))
        unrealized_pnl = float(p.get('unrealized_pl', 0))
        unrealized_pnl_pct = float(p.get('unrealized_plpc', 0)) * 100

        total_position_value += value
        total_unrealized_pnl += unrealized_pnl

        if unrealized_pnl > 0:
            winning_positions += 1
        elif unrealized_pnl < 0:
            losing_positions += 0

        pos_list.append({
            'symbol': sym,
            'qty': qty,
            'value': value,
            'pnl': unrealized_pnl,
            'pnl_pct': unrealized_pnl_pct
        })

    # Load strategy state
    state = {}
    if os.path.exists("strategy_1h_live_state.json"):
        try:
            with open("strategy_1h_live_state.json") as f:
                state = json.load(f)
        except:
            pass

    # Prepare report data
    report = {
        'timestamp': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'),
        'equity': equity,
        'cash': cash,
        'day_pnl': day_pnl,
        'day_pnl_pct': day_pnl_pct,
        'total_unrealized_pnl': total_unrealized_pnl,
        'positions_count': len(positions),
        'winning_positions': winning_positions,
        'losing_positions': losing_positions,
        'positions': pos_list,
        'strategy_active_positions': len(state),
        'allocations': {
            'positions': total_position_value,
            'cash': cash,
            'equity': equity
        }
    }

    return report

def format_telegram_message(report):
    """Format report as Telegram HTML message"""

    if not report:
        return "❌ خرابی در دریافت اطلاعات پرتفوی"

    eq = report['equity']
    cash = report['cash']
    day_pnl = report['day_pnl']
    day_pnl_pct = report['day_pnl_pct']
    total_pnl = report['total_unrealized_pnl']

    # Header
    msg = f"<b>📊 گزارش روزانه پرتفوی</b>\n"
    msg += f"<i>{report['timestamp']}</i>\n\n"

    # Account summary
    msg += f"<b>💰 حساب</b>\n"
    msg += f"├ اکوئیتی: ${eq:,.0f}\n"
    msg += f"├ نقد: ${cash:,.0f}\n"
    msg += f"├ روز P&L: ${day_pnl:+,.0f} ({day_pnl_pct:+.2f}%)\n"
    msg += f"└ کل ناحقق‌شده: ${total_pnl:+,.0f}\n\n"

    # Positions summary
    if report['positions_count'] > 0:
        msg += f"<b>📈 موقعیت‌ها</b> ({report['positions_count']} عدد)\n"

        # Top 5 positions
        sorted_pos = sorted(report['positions'], key=lambda x: x['value'], reverse=True)[:5]
        for i, pos in enumerate(sorted_pos, 1):
            sym = pos['symbol']
            value = pos['value']
            pnl_pct = pos['pnl_pct']
            emoji = "📈" if pnl_pct > 0 else "📉" if pnl_pct < 0 else "➡️"
            msg += f"{i}. {emoji} {sym}: ${value:,.0f} ({pnl_pct:+.2f}%)\n"

        msg += "\n"
    else:
        msg += "<i>بدون موقعیت فعال</i>\n\n"

    # Strategy status
    active_strat = report['strategy_active_positions']
    if active_strat > 0:
        msg += f"<b>🤖 استراتژی 1H</b>\n"
        msg += f"├ موقعیت‌های فعال: {active_strat}\n"
        msg += f"└ وضعیت: <b>✅ فعال</b>\n\n"
    else:
        msg += f"<b>🤖 استراتژی 1H</b>\n"
        msg += f"└ وضعیت: ⏸️ منتظر سیگنال\n\n"

    # Allocations
    msg += "<b>🎯 توزیع سرمایه</b>\n"
    pos_pct = (report['allocations']['positions'] / eq * 100) if eq > 0 else 0
    cash_pct = (report['allocations']['cash'] / eq * 100) if eq > 0 else 0
    msg += f"├ موقعیت‌ها: {pos_pct:.1f}%\n"
    msg += f"└ نقد: {cash_pct:.1f}%\n"

    return msg

def send_telegram(msg):
    """Send to Telegram"""
    try:
        token = cfg("TELEGRAM_BOT_TOKEN")
        chat = cfg("TELEGRAM_CHAT_ID")
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        resp = requests.post(
            url,
            data={
                "chat_id": chat,
                "text": msg,
                "parse_mode": "HTML"
            },
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("\n🚀 Portfolio Daily Report")
    print("=" * 60)

    # Analyze portfolio
    report = analyze_portfolio()

    if report:
        print(f"✅ Equity: ${report['equity']:,.0f}")
        print(f"✅ Day P&L: ${report['day_pnl']:+,.0f}")
        print(f"✅ Positions: {report['positions_count']}")
        print(f"✅ Strategy active: {report['strategy_active_positions']}")
    else:
        print("❌ Failed to retrieve portfolio data")

    # Format message
    msg = format_telegram_message(report)

    # Send to Telegram
    if send_telegram(msg):
        print("✅ Telegram report sent")
    else:
        print("⚠️  Telegram send failed")

    print("=" * 60 + "\n")
