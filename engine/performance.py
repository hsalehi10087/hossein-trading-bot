"""بازده هر ربات از روز اول کار تا امروز، از روی گزارش کارگزاری (Alpaca FILL activities).

سود کل یک ربات = فروش‌ها + ارزش فعلی موقعیت‌ها − خریدها. بازده = سود کل ÷ کل خریدها (سرمایه‌ی به‌کاررفته).
هر معامله به ربات صاحب نماد نسبت داده می‌شود (هماهنگ‌کننده همین مالکیت را اجرا می‌کند).
اختیار خرید/فروش: هر قرارداد ۱۰۰ سهم است.
"""
import datetime as dt
import re

OCC = re.compile(r'^[A-Z]{1,6}\d{6}[CP]\d{8}$')
UNOWNED = 'بی‌صاحب'


def fetch_fills(get, after='2026-01-01'):
    """همه‌ی FILLها از تاریخ after تا امروز، به‌ترتیب زمانی. get(path, params) → JSON."""
    out, token = [], None
    for _ in range(500):
        params = {'after': after, 'direction': 'asc', 'page_size': 100}
        if token:
            params['page_token'] = token
        page = get('/account/activities/FILL', params) or []
        if not page:
            break
        out += page
        token = page[-1]['id']
        if len(page) < 100:
            break
    return out


def summarize(fills, positions, owner):
    """fills: لیست FILL؛ positions: لیست موقعیت‌های باز؛ owner(symbol) → نام ربات یا None.
    خروجی: {sleeve: {'start', 'trades', 'bought', 'sold', 'value', 'pl', 'ret'}}"""
    agg = {}

    def bucket(name):
        return agg.setdefault(name, {'start': None, 'trades': 0, 'bought': 0.0, 'sold': 0.0,
                                     'value': 0.0, 'pl': 0.0, 'ret': None})

    for f in sorted(fills, key=lambda x: x['transaction_time']):
        sym = f['symbol']
        b = bucket(owner(sym) or UNOWNED)
        mult = 100 if OCC.match(sym) else 1
        amount = float(f['qty']) * float(f['price']) * mult
        day = f['transaction_time'][:10]
        b['start'] = b['start'] or day
        b['trades'] += 1
        if f['side'] == 'buy':
            b['bought'] += amount
        else:
            b['sold'] += amount
    for p in positions:
        sym = p['symbol']
        bucket(owner(sym) or UNOWNED)['value'] += float(p['market_value'])
    for name, b in agg.items():
        b['pl'] = b['sold'] + b['value'] - b['bought']
        b['ret'] = b['pl'] / b['bought'] if b['bought'] > 0 else None
    return agg


def fa_num(x):
    return f'{x:+,.0f}'.replace('-', '−')


def line(name, b, names):
    if b['ret'] is None:
        return f"{names.get(name, name)}: هنوز معامله‌ای ثبت نشده"
    return (f"{names.get(name, name)}: از {b['start']} | {b['trades']} معامله | "
            f"سود کل {fa_num(b['pl'])}$ | بازده {b['ret'] * 100:+.1f}٪ (روی {b['bought']:,.0f}$ خرید)")
