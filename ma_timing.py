"""سه ربات «زمان‌بندی با میانگین متحرک» از شکارچی روش (rules/ma_timing.yaml)، هر کدام ۵۰۰۰ دلار، حساب آزمایشی.

هر روز بعد از بسته‌شدن بازار آمریکا: برای هر ربات، با بسته‌شدن آخرین جلسه‌ی کامل نماد سیگنال و میانگین N روزه:
  بیرون → اگر بسته‌شدن > میانگین × (۱ + enter_above): خرید نماد معامله با کل سرمایه
  داخل  → اگر بسته‌شدن < میانگین × (۱ − exit_below): فروش همه
  بین دو باند: همان وضعیت قبلی (وضعیت از روی تاریخچه بازسازی می‌شود، پس به حافظه وابسته نیست)
یک بار خرید با کل سرمایه و نگه‌داشتن بدون تنظیم روزانه (مثل آزمون). سفارش بازار؛ اگر بازار بسته است در بازگشایی بعدی اجرا می‌شود.
خرید از هماهنگ‌کننده‌ی سبد اجازه می‌گیرد. پیام تلگرام فقط هنگام خرید یا فروش.
"""
import datetime as dt
import json
import os
import sys

import pandas as pd
import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import conductor as PC  # noqa: E402
from engine import data as D  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'


def load_rules(path=os.path.join(ROOT, 'rules', 'ma_timing.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    for s in r['strategies']:
        if not (20 <= s['sma'] <= 400 and 0 <= s['enter_above'] < 0.2 and 0 <= s['exit_below'] < 0.2
                and 0 < s['capital_usd'] <= 50000):
            raise ValueError(f"ma_timing rule out of range: {s['sleeve']}")
    return r


def regime(closes, n, up, down):
    """وضعیت (True = داخل بازار) با هیسترزیس، از روی کل تاریخچه‌ی بسته‌شدن‌ها (فقط داده‌ی گذشته)."""
    c = pd.Series(closes, dtype=float)
    sma = c.rolling(n).mean()
    inside = False
    for p, m in zip(c.values, sma.values):
        if m != m:
            continue
        if not inside and p > m * (1 + up):
            inside = True
        elif inside and p < m * (1 - down):
            inside = False
    return inside, float(c.iloc[-1]), float(sma.iloc[-1])


def get(path):
    r = requests.get(ENDPOINT + path, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def tg(text):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage', timeout=20,
                      data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'})


def main():
    rules, prules = load_rules(), PC.load()
    session = D.last_closed_session(ENDPOINT)
    sigs = sorted({s['signal'] for s in rules['strategies']} | {s['trade'] for s in rules['strategies']})
    closes = D.daily_closes(sigs, start=str(dt.date.today() - dt.timedelta(days=900)))
    if session is not None:
        closes = closes[closes.index <= pd.Timestamp(session)]
    acct, positions = get('/account'), get('/positions')
    pos = {p['symbol']: float(p['qty']) for p in positions}
    cond = PC.Conductor(prules, acct, positions, os.path.join(STORE, 'portfolio', 'state.json'))
    log, msgs = [], []
    rp = os.path.join(STORE, 'ma_timing', 'retired.json')
    try:
        retired = json.load(open(rp))
    except Exception:
        retired = {}
    for s in rules['strategies']:
        c = closes[s['signal']].dropna()
        if len(c) < s['sma'] + 5 or (session and c.index[-1].date() < session):
            log.append({'sleeve': s['sleeve'], 'error': 'stale or short data'})
            continue
        inside, last, sma = regime(c.values, s['sma'], s['enter_above'], s['exit_below'])
        held = pos.get(s['trade'], 0.0)
        px = float(closes[s['trade']].dropna().iloc[-1])
        rec = {'sleeve': s['sleeve'], 'session': str(c.index[-1].date()), 'signal_close': round(last, 2),
               'sma': round(sma, 2), 'gap_pct': round((last / sma - 1) * 100, 2), 'inside': inside, 'held': held}
        side, qty = None, 0
        if inside and held * px < 50:
            side, qty = 'buy', int(s['capital_usd'] // px)
            qty, _ = cond.approve(s['sleeve'], {'symbol': s['trade'], 'side': 'buy', 'qty': qty, 'price': px})
        elif not inside and held > 0:
            side, qty = 'sell', held
        if side and qty > 0:
            coid = f"mat-{s['sleeve']}-{rec['session']}-{side}"
            if not DRY:
                r = requests.post(ENDPOINT + '/orders', headers=H, timeout=30,
                                  json={'symbol': s['trade'], 'qty': str(qty), 'side': side, 'type': 'market',
                                        'time_in_force': 'day', 'client_order_id': coid})
                rec['order'] = f'{r.status_code} {r.text[:150]}'
                ok = r.status_code in (200, 201)
            else:
                ok = True
            if ok:
                act = 'خرید' if side == 'buy' else 'فروش'
                why = (f"{s['signal']} {rec['gap_pct']:+.1f}٪ نسبت به میانگین {s['sma']} روزه" +
                       (f" (باند ±{s['enter_above'] * 100:.0f}٪)" if s['enter_above'] else ''))
                msgs.append(f"📐 <b>{s['name_fa']} — {act}</b>\n{act} {qty:g} واحد {s['trade']} به قیمت حدود {px:,.2f}$ "
                            f"(حدود {qty * px:,.0f}$)\nدلیل: {why}" + (' (آزمایشی، بدون سفارش)' if DRY else ''))
        rec['action'] = side if side and qty > 0 else None
        log.append(rec)
        ret = s.get('retire')                      # فروش یک‌باره‌ی نماد ربات قبلی همین سرمایه
        if ret and pos.get(ret, 0) > 0 and ret not in retired:
            coid = f"mat-retire-{ret}-{rec['session']}"
            ok = True
            if not DRY:
                r = requests.post(ENDPOINT + '/orders', headers=H, timeout=30,
                                  json={'symbol': ret, 'qty': str(pos[ret]), 'side': 'sell', 'type': 'market',
                                        'time_in_force': 'day', 'client_order_id': coid})
                ok = r.status_code in (200, 201)
                log.append({'sleeve': s['sleeve'], 'retire': ret, 'order': f'{r.status_code} {r.text[:150]}'})
            if ok:
                retired[ret] = rec['session']
                msgs.append(f"📐 <b>{s['name_fa']}</b>\nفروش {pos[ret]:g} واحد {ret} (ربات قبلی این سرمایه) برای جابه‌جایی به {s['trade']}")
    os.makedirs(os.path.join(STORE, 'ma_timing'), exist_ok=True)
    json.dump(retired, open(rp, 'w'))
    with open(os.path.join(STORE, 'ma_timing', 'log.jsonl'), 'a', encoding='utf-8') as f:
        for r in log:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(json.dumps(log, ensure_ascii=False, indent=1))
    if msgs:
        tg('\n\n'.join(msgs) + '\n<i>حساب آزمایشی Alpaca؛ سفارش در جلسه‌ی بعدی بازار اجرا می‌شود.</i>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
