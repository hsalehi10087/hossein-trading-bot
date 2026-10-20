"""دفتر سیگنال‌های ربات یک‌ساعته (فقط خواندنی؛ هیچ سفارشی ثبت نمی‌کند).

از Alpaca همه‌ی سفارش‌های پرشده را می‌خواند، هر خرید را یک «سیگنال» حساب می‌کند و نتیجه‌اش را می‌سنجد:
  - بخش فروخته‌شده (FIFO): قیمت میانگین فروش و سود/زیان تحقق‌یافته
  - بخش باز: قیمت فعلی از /positions و سود/زیان باز
GLD متعلق به ربات روند طلاست و جدا گزارش نمی‌شود.
خروجی: datastore/strategy_1h/journal.csv و خلاصه در stdout (بدون تلگرام).
"""
import collections
import csv
import datetime as dt
import os

import requests

BASE = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
OUT = os.path.join(os.getenv('DATASTORE', 'datastore'), 'strategy_1h', 'journal.csv')
EXCLUDE = {'GLD'}
TEH = dt.timezone(dt.timedelta(hours=3, minutes=30))


def get(path, **params):
    r = requests.get(BASE + path, headers=H, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def all_filled_orders(days=120):
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    out, seen = [], set()
    while True:
        batch = get('/orders', status='closed', after=after, limit=500, direction='asc')
        new = [o for o in batch if o['id'] not in seen]
        if not new:
            break
        for o in new:
            seen.add(o['id'])
            if float(o.get('filled_qty') or 0) > 0:
                out.append(o)
        if len(batch) < 500:
            break
        after = batch[-1]['submitted_at']
    return sorted(out, key=lambda o: o['filled_at'])


def main():
    orders = [o for o in all_filled_orders() if o['symbol'] not in EXCLUDE]
    pos = {p['symbol']: float(p['current_price']) for p in get('/positions')}
    lots = collections.defaultdict(list)  # symbol -> [row]
    rows = []
    for o in orders:
        sym, q, px = o['symbol'], float(o['filled_qty']), float(o['filled_avg_price'])
        mult = 100 if o.get('asset_class') == 'us_option' else 1
        if o['side'] == 'buy':
            r = {'time_tehran': dt.datetime.fromisoformat(o['filled_at'].replace('Z', '+00:00')).astimezone(TEH)
                 .strftime('%Y-%m-%d %H:%M'), 'symbol': sym,
                 'type': 'option' if mult == 100 else 'stock', 'qty': q, 'buy_price': px, 'mult': mult,
                 'open_qty': q, 'sold_qty': 0.0, 'sold_value': 0.0}
            rows.append(r)
            lots[sym].append(r)
        else:
            left = q
            for r in lots[sym]:
                if left <= 0:
                    break
                take = min(left, r['open_qty'])
                if take > 0:
                    r['open_qty'] -= take
                    r['sold_qty'] += take
                    r['sold_value'] += take * px
                    left -= take
    total_cost = total_pl = 0.0
    for r in rows:
        cost = r['qty'] * r['buy_price'] * r['mult']
        realized = (r['sold_value'] - r['sold_qty'] * r['buy_price']) * r['mult']
        cur = pos.get(r['symbol'])
        unreal = (cur - r['buy_price']) * r['open_qty'] * r['mult'] if (cur and r['open_qty'] > 0) else 0.0
        r['status'] = 'open' if r['open_qty'] > 0 and cur else ('closed' if r['sold_qty'] >= r['qty'] else 'partial')
        r['exit_or_now'] = round(r['sold_value'] / r['sold_qty'], 2) if r['status'] == 'closed' else (round(cur, 2) if cur else '')
        r['pl_usd'] = round(realized + unreal, 2)
        r['pl_pct'] = round((realized + unreal) / cost * 100, 2) if cost else 0.0
        total_cost += cost
        total_pl += realized + unreal
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cols = ['time_tehran', 'symbol', 'type', 'qty', 'buy_price', 'status', 'exit_or_now', 'pl_usd', 'pl_pct']
    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(reversed(rows))
    print(f'signals={len(rows)} cost={total_cost:,.0f} pl={total_pl:+,.0f}')
    for r in reversed(rows[-25:]):
        print({k: r[k] for k in cols})


if __name__ == '__main__':
    main()
