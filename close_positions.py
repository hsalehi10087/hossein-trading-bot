"""بستن کامل موقعیت‌های مشخص‌شده در حساب آزمایشی (یک‌باره، با تأیید کاربر در گفتگو).

ورودی: SYMBOLS="A,B,C". فقط آدرس paper پذیرفته می‌شود. نتیجه در datastore/manual/closures.csv ثبت می‌شود.
"""
import csv
import datetime as dt
import os
import sys

import requests

BASE = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if 'paper-api' not in BASE:
    sys.exit('only paper endpoint allowed')
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
OUT = os.path.join(os.getenv('DATASTORE', 'datastore'), 'manual', 'closures.csv')
syms = [s.strip().upper() for s in os.getenv('SYMBOLS', '').split(',') if s.strip()]
pos = {p['symbol']: p for p in requests.get(BASE + '/positions', headers=H, timeout=30).json()}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
new = not os.path.exists(OUT)
with open(OUT, 'a', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    if new:
        w.writerow(['ts_utc', 'symbol', 'qty', 'market_value', 'unrealized_pl', 'status', 'order_id'])
    tot_v = tot_pl = 0.0
    for s in syms:
        p = pos.get(s)
        if not p:
            print(s, 'no position')
            w.writerow([dt.datetime.utcnow().isoformat(), s, 0, 0, 0, 'no_position', ''])
            continue
        r = requests.delete(f'{BASE}/positions/{s}', headers=H, timeout=30)
        oid = r.json().get('id', '') if r.status_code in (200, 207) else ''
        st = 'submitted' if r.status_code in (200, 207) else f'error {r.status_code} {r.text[:80]}'
        tot_v += float(p['market_value'])
        tot_pl += float(p['unrealized_pl'])
        w.writerow([dt.datetime.utcnow().isoformat(), s, p['qty'], p['market_value'], p['unrealized_pl'], st, oid])
        print(s, p['qty'], p['market_value'], p['unrealized_pl'], st)
print(f'total market value {tot_v:,.0f} | unrealized P/L {tot_pl:+,.0f}')
