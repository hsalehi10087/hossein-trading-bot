"""گزارش ماهانه‌ی ریسک سبد (ایده از pyvar-portfolio-analytics): همبستگی ربات‌ها، نوسان، VaR و افت ترکیبی.

هر ربات با نماد اصلی‌اش جایگزین می‌شود و فرض «بدترین حالت» این است که همه هم‌زمان کامل در بازار باشند
(وزن = سرمایه‌ی تعیین‌شده در rules/portfolio.yaml). کپی ترید و ربات ساعتی (نمادهای متغیر) کنار گذاشته می‌شوند.
تمرکز واقعی هم از موقعیت‌های فعلی حساب Alpaca حساب می‌شود. پیام تلگرام اولین یکشنبه‌ی هر ماه.
"""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import conductor as PC  # noqa: E402

STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
PROXY = {'gld_trend': 'GLD', 'qqq_trend': 'QQQ', 'spy_band': 'SPY', 'spy_10m': 'VOO', 'qqq_200': 'QQQM',
         'btc_momentum': 'BTC-USD', 'eth_weekend': 'ETH-USD'}
NAMES = {'gld_trend': 'طلا', 'qqq_trend': 'روند QQQ', 'spy_band': 'باند SPY', 'spy_10m': '۱۰ ماهه VOO',
         'qqq_200': 'QQQ۲۰۰', 'btc_momentum': 'مومنتوم BTC', 'eth_weekend': 'آخر هفته ETH'}


def fa(x):
    return str(x).translate(str.maketrans('0123456789.-', '۰۱۲۳۴۵۶۷۸۹٫−'))


def analyse(closes, weights):
    """closes: DataFrame قیمت (ستون = ربات)، weights: dict سرمایه. خروجی: آمار ریسک سبد ترکیبی."""
    rets = closes.ffill().pct_change().dropna(how='all').fillna(0.0)
    w = pd.Series(weights, dtype=float)[rets.columns]
    w = w / w.sum()
    port = rets @ w
    eq = (1 + port).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    corr = rets.corr()
    pairs = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            pairs.append((a, b, float(corr.loc[a, b])))
    pairs.sort(key=lambda t: -t[2])
    vol_each = (rets.std() * np.sqrt(252)).to_dict()
    contrib = (w * (rets.cov() @ w)) / float(w @ rets.cov() @ w)       # سهم هر ربات از ریسک کل
    return {'days': len(rets), 'ann_return': float(eq.iloc[-1] ** (252 / len(rets)) - 1),
            'ann_vol': float(port.std() * np.sqrt(252)), 'var95_1d': float(-np.percentile(port, 5)),
            'max_dd': dd, 'pairs': pairs, 'vol_each': vol_each, 'risk_share': contrib.to_dict()}


def text(res, weights, conc):
    tot = sum(weights.values())
    L = ['🧮 <b>گزارش ماهانه‌ی ریسک سبد</b> (۱ سال گذشته، فرض: همه‌ی ربات‌ها کامل در بازار)',
         f"ترکیب {fa(len(weights))} ربات با {fa(f'{tot:,.0f}')}$: نوسان سالانه {fa(f'{res['ann_vol'] * 100:.0f}')}٪ | "
         f"بیشترین افت {fa(f'{res['max_dd'] * 100:.0f}')}٪ | ضرر یک روز بد (۵٪ بدترین روزها) حدود "
         f"{fa(f'{res['var95_1d'] * tot:,.0f}')}$"]
    hi = [p for p in res['pairs'] if p[2] >= 0.8]
    if hi:
        L.append('🔗 ربات‌های خیلی هم‌جهت (همبستگی ≥ ۰٫۸؛ در ریزش با هم ضرر می‌دهند):')
        L += [f"  • {NAMES[a]} و {NAMES[b]}: {fa(f'{c:.2f}')}" for a, b, c in hi]
    lo = [p for p in res['pairs'] if p[2] < 0.2][-3:]
    if lo:
        L.append('🛡 کم‌همبستگی (پخش‌کننده‌ی ریسک): ' + '، '.join(f"{NAMES[a]}/{NAMES[b]} {fa(f'{c:.2f}')}" for a, b, c in lo))
    share = sorted(res['risk_share'].items(), key=lambda t: -t[1])[:3]
    L.append('⚖️ بیشترین سهم از ریسک کل: ' + '، '.join(f"{NAMES[k]} {fa(f'{v * 100:.0f}')}٪" for k, v in share))
    if conc:
        L.append('📍 بزرگ‌ترین نمادهای حساب الان: ' + '، '.join(f"{s} {fa(f'{p * 100:.0f}')}٪" for s, p in conc[:4]) +
                 ' (سقف هر نماد ۳۰٪)')
    L.append('<i>فقط گزارش؛ قاعده‌ای عوض نمی‌شود. حساب آزمایشی.</i>')
    return '\n'.join(L)


def concentration():
    try:
        acct = requests.get(ENDPOINT + '/account', headers=H, timeout=30).json()
        pos = requests.get(ENDPOINT + '/positions', headers=H, timeout=30).json()
        eq = float(acct['equity'])
        c = sorted(((p['symbol'], abs(float(p['market_value'])) / eq) for p in pos), key=lambda t: -t[1])
        return c
    except Exception as e:
        print('alpaca', e, file=sys.stderr)
        return []


def tg(t):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage', timeout=20,
                      data={'chat_id': c, 'text': t, 'parse_mode': 'HTML'})


def main():
    import yfinance as yf
    r = PC.load()
    weights = {k: r['sleeves'][k]['capital_usd'] for k in PROXY if k in r['sleeves']}
    syms = [PROXY[k] for k in weights]
    px = yf.download(syms, period='13mo', auto_adjust=True, progress=False)['Close']
    px = px.rename(columns={v: k for k, v in PROXY.items()})[list(weights)]
    px = px[px.index >= px.index[-1] - pd.Timedelta(days=365)]
    res = analyse(px, weights)
    conc = concentration()
    out = text(res, weights, conc)
    print(out)
    os.makedirs(os.path.join(STORE, 'portfolio'), exist_ok=True)
    json.dump({'date': str(dt.date.today()), **{k: v for k, v in res.items() if k != 'pairs'},
               'pairs': [list(p) for p in res['pairs']], 'concentration': conc},
              open(os.path.join(STORE, 'portfolio', 'risk_report.json'), 'w'), ensure_ascii=False, indent=1)
    if os.getenv('FORCE_SEND') == 'true' or dt.date.today().day <= 7:
        tg(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
