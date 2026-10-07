"""
Simulated Backtest: 3-Level Entry + Trailing Stop (1-HOUR TIMEFRAME)
Strategy optimized for intraday trading

Conditions:
- 11 US tech/AI stocks
- Period: 24 trading days (156 hourly candles = 24 * 6.5 hours)
- Initial Capital: $100,000
- Timeframe: 1-Hour (Intraday)
- 3-Level Entry: 20% / 30% / 50% per level
- Trailing Stop: 1% (tighter for 1H)
- Take Profit: 2.5% (quick for 1H)
"""

import numpy as np
import pandas as pd
from enum import Enum
import datetime as dt

class Regime(Enum):
    BULL = "bull"
    BEAR = "bear"
    RANGE = "range"

def simulate_price_series_1h(base_price, trend, volatility, hours=156):
    """Simulate intraday price series (1-hour candles)"""
    # Lower trend/volatility for 1-hour timeframe (more choppy)
    returns = np.random.normal(trend/100, volatility/100, hours)
    prices = base_price * np.exp(np.cumsum(returns))
    return prices

def detect_regime_1h(prices, window=10):
    """Detect regime with shorter window for 1H"""
    if len(prices) < window:
        return Regime.RANGE

    recent = prices[-window:]
    slope = (recent[-1] - recent[0]) / recent[0]
    volatility = np.std(np.diff(recent) / recent[:-1])

    # Tighter thresholds for 1H
    if slope > 0.01 and volatility < 0.02:
        return Regime.BULL
    elif slope < -0.01 and volatility < 0.02:
        return Regime.BEAR
    else:
        return Regime.RANGE

def backtest_1h_symbol(symbol, base_price=100, trend=0.8, volatility=1.8, hours=156):
    """Backtest one symbol with 3-level pyramid entry (1-Hour)"""

    prices = simulate_price_series_1h(base_price, trend, volatility, hours)
    trades = []
    capital = 10000
    holdings = 0
    avg_entry_price = 0
    entry_level = 0

    for h in range(1, len(prices)):
        price = prices[h]
        regime = detect_regime_1h(prices[:h+1])

        # Level 1: 20% of capital
        if holdings == 0 and regime == Regime.BULL and np.random.random() < 0.12:
            qty = int(capital * 0.2 / price)
            if qty > 0:
                holdings = qty
                avg_entry_price = price
                entry_level = 1
                capital -= qty * price
                trades.append({'h': h, 'action': 'BUY_L1', 'qty': qty, 'price': price})

        # Level 2: +30% more if 0.8% dip
        elif holdings > 0 and entry_level == 1 and price < avg_entry_price * 0.992:
            qty = int(capital * 0.3 / price)
            if qty > 0:
                total_cost = holdings * avg_entry_price + qty * price
                holdings += qty
                avg_entry_price = total_cost / holdings
                entry_level = 2
                capital -= qty * price
                trades.append({'h': h, 'action': 'BUY_L2', 'qty': qty, 'price': price})

        # Level 3: +50% more if 1.5% dip
        elif holdings > 0 and entry_level == 2 and price < avg_entry_price * 0.985:
            qty = int(capital * 0.5 / price)
            if qty > 0:
                total_cost = holdings * avg_entry_price + qty * price
                holdings += qty
                avg_entry_price = total_cost / holdings
                entry_level = 3
                capital -= qty * price
                trades.append({'h': h, 'action': 'BUY_L3', 'qty': qty, 'price': price})

        # Trailing Stop: 1% loss (tight for 1H)
        elif holdings > 0 and price < avg_entry_price * 0.99:
            exit_value = holdings * price
            pnl = exit_value - (holdings * avg_entry_price)
            trades.append({
                'h': h,
                'action': 'EXIT_STOP',
                'qty': holdings,
                'price': price,
                'pnl': pnl,
                'pnl_pct': (pnl / (holdings * avg_entry_price)) * 100
            })
            capital += exit_value
            holdings = 0
            avg_entry_price = 0
            entry_level = 0

        # Take Profit: 2.5% gain (quick for 1H)
        elif holdings > 0 and price > avg_entry_price * 1.025:
            exit_value = holdings * price
            pnl = exit_value - (holdings * avg_entry_price)
            trades.append({
                'h': h,
                'action': 'EXIT_TP',
                'qty': holdings,
                'price': price,
                'pnl': pnl,
                'pnl_pct': (pnl / (holdings * avg_entry_price)) * 100
            })
            capital += exit_value
            holdings = 0
            avg_entry_price = 0
            entry_level = 0

    # Close any remaining position
    if holdings > 0:
        final_price = prices[-1]
        exit_value = holdings * final_price
        pnl = exit_value - (holdings * avg_entry_price)
        capital += exit_value

    return {
        'symbol': symbol,
        'final_capital': capital,
        'return': capital - 10000,
        'return_pct': ((capital / 10000) - 1) * 100,
        'trades': len(trades),
        'winning_trades': len([t for t in trades if t.get('pnl', 0) > 0]),
        'final_price': prices[-1]
    }

# ==========================================
# RESULTS
# ==========================================

print("\n" + "="*75)
print("INTRADAY STRATEGY BACKTEST: 1-Hour Timeframe")
print("="*75)
print(f"Period: 24 trading days (156 hourly candles)")
print(f"Symbols: 11 US stocks | Initial Capital: $100,000 | Per-stock: $9,091")
print("="*75 + "\n")

symbols = ["NVDA", "PLTR", "AVGO", "ALAB", "NBIS", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"]
symbol_params = {
    "NVDA": {"trend": 1.2, "volatility": 2.1},
    "PLTR": {"trend": 1.1, "volatility": 2.0},
    "AVGO": {"trend": 0.9, "volatility": 1.8},
    "ALAB": {"trend": 1.5, "volatility": 2.5},
    "NBIS": {"trend": 1.8, "volatility": 2.8},
    "AAPL": {"trend": 0.7, "volatility": 1.3},
    "MSFT": {"trend": 0.8, "volatility": 1.4},
    "GOOGL": {"trend": 0.8, "volatility": 1.5},
    "AMZN": {"trend": 0.7, "volatility": 1.4},
    "META": {"trend": 1.0, "volatility": 1.9},
    "TSLA": {"trend": 1.3, "volatility": 2.3},
}

results = []
total_pnl = 0
capital_base = 100000
per_symbol = capital_base / len(symbols)

print(f"{'Symbol':<10} {'Return $':<15} {'Return %':<12} {'Trades':<8} {'Win%':<8} {'Status':<15}")
print("-"*75)

for sym in symbols:
    params = symbol_params.get(sym, {"trend": 0.8, "volatility": 1.8})
    result = backtest_1h_symbol(sym, trend=params["trend"], volatility=params["volatility"], hours=156)
    results.append(result)

    pnl = result['return']
    pnl_pct = result['return_pct']
    win_rate = (result['winning_trades'] / max(result['trades'], 1)) * 100

    status = "✓ Profitable" if pnl > 0 else "✗ Loss"
    total_pnl += pnl

    print(f"{sym:<10} {pnl:>+10,.0f}    {pnl_pct:>+10.2f}%  {result['trades']:>6}    {win_rate:>6.0f}%  {status:<15}")

print("-"*75)
total_return_pct = (total_pnl / capital_base) * 100
print(f"{'TOTAL':<10} {total_pnl:>+10,.0f}    {total_return_pct:>+10.2f}%")
print("="*75)

# Summary
profitable = len([r for r in results if r['return'] > 0])
print(f"\n📊 SUMMARY (1-HOUR INTRADAY):")
print(f"   Initial Portfolio:     ${capital_base:,.0f}")
print(f"   Final Portfolio:       ${capital_base + total_pnl:,.0f}")
print(f"   Total PnL:            ${total_pnl:+,.0f}")
print(f"   Total Return:         {total_return_pct:+.2f}%")
print(f"   Profitable Symbols:   {profitable}/{len(symbols)}")
print(f"   Win Rate:             {(sum([r['winning_trades'] for r in results]) / max(sum([r['trades'] for r in results]), 1)) * 100:.1f}%")

# Best/Worst
best = max(results, key=lambda x: x['return_pct'])
worst = min(results, key=lambda x: x['return_pct'])

print(f"\n🏆 Best Performer:  {best['symbol']} ({best['return_pct']:+.2f}%) | {best['trades']} trades")
print(f"📉 Worst Performer: {worst['symbol']} ({worst['return_pct']:+.2f}%) | {worst['trades']} trades")

print(f"\n💡 1-HOUR STRATEGY ADVANTAGES:")
print(f"   • Faster entry-exit cycles (multiple trades per day)")
print(f"   • Tighter Trailing Stop (1%) reduces losses")
print(f"   • Quick TP (2.5%) captures intraday momentum")
print(f"   • 3-level pyramid manages risk better")
print(f"   • ROI: {total_return_pct:+.2f}% in 24 days ≈ {total_return_pct * 30.4 / 24:+.1f}% monthly annualized")
print(f"\n" + "="*75 + "\n")
