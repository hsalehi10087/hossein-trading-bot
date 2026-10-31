"""اجرای شبانه‌ی هماهنگ همه‌ی ربات‌ها + یک گزارش تلگرام یکجا.

ترتیب: آخرین جلسه‌ی بسته‌شده‌ی بازار → وضعیت حساب و موقعیت‌ها → بررسی موقعیت‌های بی‌صاحب →
ربات طلا → هسته‌ی QQQ (هر سفارش از هماهنگ‌کننده اجازه می‌گیرد) → ذخیره‌ی وضعیت سبد → گزارش.
"""
import csv
import datetime as dt
import os
import sys
import traceback

import requests

import gld_trend
import qqq_trend
from engine import conductor as PC
from engine import data as D

STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
NAMES = {'gld_trend': '🥇 روند طلا', 'qqq_trend': '📈 هسته‌ی QQQ', 'legacy_1h': '⏱ ربات ساعتی', 'copy_trade': '👥 کپی ترید'}


def get(path):
    r = requests.get(ENDPOINT + path, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    rules = PC.load()
    acct, positions = get('/account'), get('/positions')
    session = D.last_closed_session(ENDPOINT)
    cond = PC.Conductor(rules, acct, positions, os.path.join(STORE, 'portfolio', 'state.json'),
                        kill_env=os.getenv('KILL_SWITCH', 'false').lower() == 'true')
    orphans = cond.orphans()
    results, errors = [], []
    for mod in (gld_trend, qqq_trend):
        try:
            results.append(mod.main(conductor=cond, session=session, notify=False))
        except Exception as e:
            traceback.print_exc()
            errors.append(f'{mod.__name__}: {e!r}'[:200])
    cond.save_state()

    # بعد از تصمیم‌ها: ارزش هر سهمیه از موقعیت‌های واقعی
    acct2, pos2 = get('/account'), get('/positions')
    by_sleeve = {}
    for p in pos2:
        s = PC.owner_of(rules, p['symbol']) or 'بی‌صاحب'
        v = by_sleeve.setdefault(s, {'value': 0.0, 'upl': 0.0})
        v['value'] += float(p['market_value'])
        v['upl'] += float(p['unrealized_pl'])

    eq, cash = float(acct2['equity']), float(acct2['cash'])
    lines = [f'<b>🧭 گزارش سبد — حساب آزمایشی</b> — جلسه‌ی {session}',
             f'ارزش حساب: <b>{eq:,.0f}$</b> | نقد: {cash:,.0f}$ | فاصله از سقف: {cond.drawdown * 100:+.1f}٪'
             + (' | 🛑 ترمز ریزش فعال' if cond.brake else '') + (' | ⛔ کلید توقف' if cond.kill else ''), '']
    lines.append('<b>ربات‌ها:</b>')
    for name, s in rules['sleeves'].items():
        v = by_sleeve.get(name, {'value': 0.0, 'upl': 0.0})
        lines.append(f"{NAMES.get(name, name)}: سرمایه {s['capital_usd']:,}$ | در بازار {v['value']:,.0f}$ | "
                     f"سود/زیان باز {v['upl']:+,.0f}$")
    if orphans:
        lines += ['', '<b>⚠️ موقعیت بی‌صاحب (هیچ رباتی مسئولش نیست):</b>'] + \
                 [f'{s}: {q:g} واحد، {v:,.0f}$' for s, q, v in orphans]
    for r in results:
        lines += [''] + r['lines']
    if errors:
        lines += ['', '<b>❌ خطا:</b>'] + errors

    os.makedirs(os.path.join(STORE, 'portfolio'), exist_ok=True)
    daily = os.path.join(STORE, 'portfolio', 'daily.csv')
    new = not os.path.exists(daily)
    with open(daily, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['ts_utc', 'session', 'equity', 'cash', 'drawdown', 'brake', 'orphans'] +
                       [f'{n}_value' for n in rules['sleeves']])
        w.writerow([dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), session, round(eq, 2),
                    round(cash, 2), round(cond.drawdown, 4), cond.brake, len(orphans)] +
                   [round(by_sleeve.get(n, {}).get('value', 0.0), 2) for n in rules['sleeves']])
    msg = '\n'.join(lines)
    print(msg)
    gld_trend.tg(msg)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
