"""موتور تصمیم خالص: داده + قوانین + موقعیت فعلی ← شیء Decision.

هیچ ورودی انسانی لحظه‌ای وارد این تابع نمی‌شود؛ تنها ورودی‌ها داده‌ی بازار، فایل قوانین تأییدشده و
وضعیت حساب‌اند. خروجی همیشه یکسان است برای ورودی یکسان (قابل بازپخش/replay).
"""
import math

import pandas as pd


def atr(df, n=14):
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(), (df.Low - df.Close.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def trend_states(df, n, m, k, atr_n=14):
    """وضعیت روزبه‌روز (۰/۱) و دلیل هر تغییر. هر روز فقط از داده‌ی همان روز و قبل از آن استفاده می‌کند."""
    hi = df.High.rolling(n).max().shift()
    lo = df.Low.rolling(m).min().shift()
    a = atr(df, atr_n)
    cur, peak, out = 0, 0.0, []
    for c, h, l, av in zip(df.Close, hi, lo, a):
        why = 'hold_in' if cur else 'hold_out'
        if cur == 0 and c > h:
            cur, peak, why = 1, c, 'entry_breakout'
        elif cur == 1:
            peak = max(peak, c)
            if c < l:
                cur, why = 0, 'exit_channel'
            elif k > 0 and c < peak - k * av:
                cur, why = 0, 'exit_atr_stop'
        out.append((cur, peak, why))
    return pd.DataFrame(out, index=df.index, columns=['state', 'peak', 'why']), hi, lo, a


def decide(rules, gold, gld_price, held_qty, code_commit='local', dry_run=False):
    p, s = rules['params'], rules['sizing']
    n, m, k = p['n_entry'], p['m_exit'], float(p['atr_k'])
    st, hi, lo, a = trend_states(gold, n, m, k, p['atr_n'])
    last = st.iloc[-1]
    sig, why = int(last.state), last.why
    close, a14 = float(gold.Close.iloc[-1]), float(a.iloc[-1])
    vol = float(gold.Close.pct_change().tail(p['vol_window']).std() * math.sqrt(252))
    size = min(1.0, p['target_vol'] / vol) if vol > 0 else 0.0
    target = int(s['sleeve_usd'] * sig * size // gld_price)
    diff = target - held_qty
    threshold = max(1, math.ceil(s['rebalance_min_frac'] * target))
    need = (target == 0 and held_qty > 0) or (target > 0 and abs(diff) >= threshold)
    side = ('buy' if diff > 0 else 'sell') if need and diff != 0 else None
    data_date = str(gold.index[-1].date())
    did = f"{rules['strategy']}-v{rules['version']}-{data_date}" + ('-dry' if dry_run else '')
    reason = {
        'rule': why,
        'close': round(close, 2),
        'entry_level_today': round(float(hi.iloc[-1]), 2),
        'exit_level_today': round(float(lo.iloc[-1]), 2),
        'stop_level_today': round(float(last.peak) - k * a14, 2) if sig and k > 0 else None,
        'next_entry_level': round(float(gold.High.tail(n).max()), 2),
        'next_exit_level': round(float(gold.Low.tail(m).min()), 2),
        'atr': round(a14, 2), 'vol_annual': round(vol, 4), 'size_frac': round(size, 4),
        'gld_price': round(float(gld_price), 2), 'held_qty': int(held_qty), 'target_qty': target,
        'diff': diff, 'rebalance_threshold': threshold,
        'params': {'n_entry': n, 'm_exit': m, 'atr_k': k, 'target_vol': p['target_vol']},
    }
    return {
        'decision_id': did, 'strategy': rules['strategy'], 'rule_version': rules['version'],
        'code_commit': code_commit, 'data_date': data_date, 'symbol': rules['trade_symbol'],
        'signal': sig, 'action': side or 'none', 'qty': abs(diff) if side else 0, 'dry_run': dry_run,
        'reason': reason,
    }
