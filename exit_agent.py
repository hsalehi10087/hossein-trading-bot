"""ربات خروج: فقط فروش موقعیت‌های بی‌صاحب‌شده‌ی ربات قدیمی بر اساس rules/exit_only.yaml.

هیچ خریدی ندارد. هر فروش باید از هماهنگ‌کننده‌ی سبد اجازه بگیرد. خروجی: خطوط گزارش فارسی + ثبت در portfolio/exits.csv.
"""
import csv
import datetime as dt
import json
import os
import sys

import yaml

from engine import conductor as PC
from engine import exit_rules as X
from engine.broker import AlpacaPaper

ROOT = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(ROOT, 'rules', 'exit_only.yaml')
STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
REASON_FA = {'hard_stop': 'توقف سخت: ۲۰٪ زیر میانگین خرید',
             'trailing_stop': 'توقف متحرک: ۱۲٪ زیر بالاترین قیمت از زمان ورود',
             'option_stop': 'توقف اختیار: ۴۰٪ زیان روی قرارداد',
             'option_take_profit': 'حد سود اختیار: ۱۰۰٪ سود',
             'expiry_window': 'نزدیک سررسید (۱۰ روز یا کمتر)'}


def load_rules(path=RULES):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    need = {'trail_stock', 'hard_stop_stock', 'option_stop', 'option_take_profit', 'option_exit_days'}
    errs = [] if r.get('stage') in ('paper', 'live') else ['stage must be paper or live']
    errs += [f'missing param {k}' for k in need - set(r.get('params', {}))]
    if errs:
        raise ValueError('invalid exit rules: ' + '; '.join(errs))
    return r


def main(conductor=None, session=None, notify=False):
    r = load_rules()
    p = r['params']
    rules = conductor.r if conductor is not None else PC.load()
    broker = AlpacaPaper(ENDPOINT, os.getenv('ALPACA_KEY', ''), os.getenv('ALPACA_SECRET', ''))
    state_path = os.path.join(STORE, 'portfolio', 'exit_state.json')
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    today = dt.date.today()
    lines, sells = [], 0

    for pos in broker.positions():
        sym = pos['symbol']
        if PC.owner_of(rules, sym) != 'exit_only':
            continue
        qty = float(pos['qty'])
        avg, price, plpc = float(pos['avg_entry_price']), float(pos['current_price']), float(pos['unrealized_plpc'])
        is_opt = X.is_option(sym)
        peak = max(float(state.get(sym, 0)), price, avg)
        if not is_opt:
            state[sym] = peak
        action, reason = X.decide(sym, qty, avg, price, plpc, peak, today, p)
        if action != 'sell':
            lines.append(f'{sym}: نگه‌داری ({price:,.2f}، زیان/سود {plpc * 100:+.1f}٪)')
            continue

        q = int(qty)
        if q < 1:
            lines.append(f'{sym}: فروش لازم است ولی تعداد کمتر از ۱ است؛ دستی بررسی شود')
            continue
        order = {'symbol': sym, 'side': 'sell', 'qty': q, 'price': price}
        coid = f'exit-{today.isoformat()}-{sym}'
        if broker.order_by_client_id(coid):
            lines.append(f'{sym}: سفارش امروز قبلاً ثبت شده')
            continue
        approved, checks = conductor.approve('exit_only', order) if conductor is not None else (q, [])
        if approved == 0:
            lines.append(f'{sym}: ⛔ فروش مسدود شد ({", ".join(c["name"] for c in checks if not c["passed"])})')
            continue
        status, oid, resp = 'dry_run', '', ''
        if not DRY:
            ok, oid, resp = broker.submit(sym, 'sell', approved, coid)
            status = 'submitted' if ok else f'rejected {resp[:60]}'
        sells += 1
        lines.append(f'{sym}: 🚪 فروش {approved} واحد — {REASON_FA[reason]} ({status})')
        _log(today, sym, approved, reason, status, oid, price, plpc)

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    json.dump(state, open(state_path, 'w'), indent=1)
    if not lines:
        lines = ['موقعیت بازمانده‌ای برای ربات خروج نیست.']
    return {'sleeve': 'exit_only', 'stale': False, 'lines': ['<b>🚪 ربات خروج</b>'] + lines, 'sells': sells}


def _log(today, sym, qty, reason, status, oid, price, plpc):
    path = os.path.join(STORE, 'portfolio', 'exits.csv')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['date', 'symbol', 'qty', 'reason', 'status', 'order_id', 'price', 'plpc'])
        w.writerow([today.isoformat(), sym, qty, reason, status, oid, price, round(plpc, 4)])


if __name__ == '__main__':
    res = main(notify=True)
    print('\n'.join(res['lines']))
    sys.exit(0)
