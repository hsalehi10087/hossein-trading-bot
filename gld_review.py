"""بازبینی پارامترهای استراتژی روند طلا با روش walk-forward — هر ۳ هفته یک بار.

هفته‌ای یک بار اجرا می‌شود ولی فقط اگر ≥۲۱ روز از آخرین بازبینی گذشته باشد کار می‌کند (FORCE=true برای اجرای فوری).
روش: همه‌ی ترکیب‌ها روی ۶ سال آخرِ تا یک سال پیش رتبه‌بندی می‌شوند (آموزش)، سپس روی ۱۲ ماه اخیر که در انتخاب
استفاده نشده (آزمون) سنجیده می‌شوند. پیشنهاد تغییر فقط وقتی داده می‌شود که برنده هم در آموزش (شارپ ≥ ۰.۱ بهتر)
و هم در آزمون (نه بدتر) از پارامترهای فعلی بهتر باشد. هیچ پارامتری خودکار عوض نمی‌شود؛ فقط پیشنهاد به تلگرام می‌رود.
"""
import datetime as dt
import itertools
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS = os.path.join(HERE, 'gld_params.json')
STATE = os.path.join(HERE, 'data', 'gld_param_review.json')
COST, TARGET_VOL = 0.0003, 0.12
FORCE = os.getenv('FORCE', 'false').lower() == 'true'


def flat(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[['High', 'Low', 'Close']].dropna()


def atr(df, n=14):
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(), (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def positions(df, r, n, m, k):
    hi, lo, a = df.High.rolling(n).max().shift(), df.Low.rolling(m).min().shift(), atr(df)
    pos, cur, peak = [], 0, 0.0
    for c, h, l, av in zip(df.Close, hi, lo, a):
        if cur == 0 and c > h:
            cur, peak = 1, c
        elif cur == 1:
            peak = max(peak, c)
            if c < l or (k and c < peak - k * av):
                cur = 0
        pos.append(cur)
    p = pd.Series(pos, df.index, dtype=float)
    vol = r.rolling(20).std() * math.sqrt(252)
    return p * (TARGET_VOL / vol).clip(upper=1.0).fillna(0)


def metrics(pos, r):
    pos = pos.reindex(r.index).fillna(0)
    s = pos.shift(1).fillna(0) * r - COST * pos.diff().abs().fillna(0)
    if s.std() == 0:
        return {'cagr': 0.0, 'sharpe': 0.0, 'max_dd': 0.0, 'trades': 0}
    eq = (1 + s).cumprod()
    return {'cagr': round((eq.iloc[-1] ** (252 / len(s)) - 1) * 100, 2),
            'sharpe': round(float(s.mean() / s.std() * math.sqrt(252)), 2),
            'max_dd': round(float((eq / eq.cummax() - 1).min() * 100), 2),
            'trades': int(((pos > 0) & (pos.shift(1).fillna(0) == 0)).sum())}


def tg(text):
    tok, chats = os.getenv('TELEGRAM_BOT_TOKEN', ''), [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip()]
    for c in chats:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                      data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'}, timeout=20)


def main():
    today = dt.date.today()
    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else {}
    last = state.get('last_review')
    if last and not FORCE and (today - dt.date.fromisoformat(last)).days < 21:
        print(f'skip: last review {last}')
        return
    cur = json.load(open(PARAMS, encoding='utf-8'))
    cur_key = (int(cur['n_entry']), int(cur['m_exit']), float(cur['atr_k']))
    df = flat(yf.download('GC=F', start='2010-01-01', interval='1d', progress=False, auto_adjust=False))
    r = df.Close.pct_change().fillna(0)
    end_train = df.index[-1] - pd.Timedelta(days=365)
    train = r[end_train - pd.Timedelta(days=6 * 365):end_train]
    test = r[end_train + pd.Timedelta(days=1):]
    grid = list(itertools.product([20, 30, 40, 55], [10, 15, 20], [0.0, 2.0, 3.0]))
    if cur_key not in grid:
        grid.append(cur_key)
    rows = []
    for key in grid:
        p = positions(df, r, *key)
        rows.append({'key': key, 'train': metrics(p, train), 'test': metrics(p, test)})
    best = max(rows, key=lambda x: x['train']['sharpe'])
    curr = next(x for x in rows if x['key'] == cur_key)
    better = (best['key'] != cur_key and best['train']['sharpe'] - curr['train']['sharpe'] >= 0.1
              and best['test']['sharpe'] >= curr['test']['sharpe'])

    def fmt(k):
        return f'سقف {k[0]} روزه / کف {k[1]} روزه / حد ضرر {k[2]:g}×ATR' if k[2] else f'سقف {k[0]} روزه / کف {k[1]} روزه / بدون حد ضرر ATR'

    def line(lbl, x):
        return (f"{lbl}: آموزش شارپ {x['train']['sharpe']} (رشد {x['train']['cagr']}٪، افت {x['train']['max_dd']}٪) | "
                f"آزمون ۱۲ ماه شارپ {x['test']['sharpe']} (رشد {x['test']['cagr']}٪، افت {x['test']['max_dd']}٪)")

    msg = [f'<b>🥇 بازبینی ۳ هفته‌ای پارامترهای استراتژی طلا</b> — {today}',
           f'<i>آموزش: {train.index[0].date()} تا {train.index[-1].date()} | آزمون: {test.index[0].date()} تا {test.index[-1].date()}</i>', '',
           f'فعلی: {fmt(cur_key)}', line('  نتیجه', curr), '']
    if better:
        msg += [f'پیشنهادی: {fmt(best["key"])}', line('  نتیجه', best), '',
                '<b>✅ پیشنهاد تغییر.</b> هیچ تغییری خودکار اعمال نمی‌شود.',
                'برای اعمال، در گفتگو با Claude بنویسید: «تأیید پارامترهای طلا».']
        status = 'pending_approval'
    else:
        msg += ['<b>⏸ پارامترهای فعلی حفظ می‌شوند.</b> ترکیب بهتری که هم در آموزش و هم در آزمون بهتر باشد پیدا نشد.']
        status = 'keep'
    tg('\n'.join(msg))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    state.update({'last_review': today.isoformat(), 'status': status,
                  'current': {'key': list(cur_key), 'train': curr['train'], 'test': curr['test']},
                  'candidate': {'key': list(best['key']), 'train': best['train'], 'test': best['test']} if better else None})
    state.setdefault('history', []).append({'date': today.isoformat(), 'status': status,
                                            'candidate': list(best['key']) if better else None})
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n'.join(msg))


if __name__ == '__main__':
    main()
