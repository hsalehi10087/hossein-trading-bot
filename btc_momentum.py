"""ربات مومنتوم ۲۸ روزه‌ی بیت‌کوین (حساب آزمایشی Alpaca).

قاعده: با آخرین کندل روزانه‌ی بسته‌شده (۰۰:۰۰ UTC)، اگر بسته‌شدن امروز از بسته‌شدن ۲۸ روز قبل بالاتر است → کل سرمایه در
بیت‌کوین؛ اگر پایین‌تر است → همه فروخته می‌شود. روزی یک بار. هر سفارش از هماهنگ‌کننده‌ی سبد (btc_momentum) اجازه می‌گیرد.
پیام تلگرام فقط وقتی معامله‌ای انجام شود؛ وضعیت روزانه در گزارش واحد ۱۹:۱۵ می‌آید.
"""
import datetime as dt
import json
import os
import sys

import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import conductor as PC  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
DATA = 'https://data.alpaca.markets/v1beta3/crypto/us/bars'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
SLEEVE = 'btc_momentum'


def load_rules(path=os.path.join(ROOT, 'rules', 'btc_momentum.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    for k in ('version', 'symbol', 'lookback_days', 'capital_usd'):
        if k not in r:
            raise ValueError(f'btc rules missing {k}')
    if not (5 <= r['lookback_days'] <= 200 and 0 < r['capital_usd'] <= 50000):
        raise ValueError('btc rules out of range')
    return r


def signal(closes, n):
    """closes: فهرست بسته‌شدن‌های روزانه‌ی کامل (قدیمی → جدید). خروجی: (۱ خرید، ۰ بیرون، بازده n روزه)."""
    if len(closes) <= n:
        raise ValueError('not enough bars')
    roc = closes[-1] / closes[-1 - n] - 1
    return (1 if roc > 0 else 0), roc


def target_qty(sig, capital, price):
    return round(capital / price, 6) if sig else 0.0


def daily_closes(symbol, days=60):
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime('%Y-%m-%dT00:00:00Z')
    r = requests.get(DATA, headers=H, timeout=30, params={'symbols': symbol, 'timeframe': '1Day', 'start': start, 'limit': 1000})
    r.raise_for_status()
    bars = r.json()['bars'][symbol]
    today = dt.datetime.now(dt.timezone.utc).date()
    full = [b for b in bars if dt.date.fromisoformat(b['t'][:10]) < today]   # فقط کندل‌های بسته‌شده
    return [b['t'][:10] for b in full], [float(b['c']) for b in full]


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
    sym = rules['symbol']
    dates, closes = daily_closes(sym)
    sig, roc = signal(closes, rules['lookback_days'])
    price = closes[-1]
    acct, positions = get('/account'), get('/positions')
    held = sum(float(p['qty']) for p in positions if p['symbol'] in ('BTCUSD', 'BTC/USD'))
    want = target_qty(sig, rules['capital_usd'], price)
    state = {'date': dates[-1], 'roc_pct': round(roc * 100, 2), 'signal': sig, 'price': price, 'held': held, 'target': want}
    msg = None
    # مثل آزمون: یک بار ورود با کل سرمایه، نگه‌داشتن بدون تنظیم روزانه، خروج کامل وقتی سیگنال منفی شد
    side = None
    if sig == 1 and held * price < rules.get('min_trade_usd', 50):
        side, qty = 'buy', want
    elif sig == 0 and held * price >= rules.get('min_trade_usd', 50):
        side, qty = 'sell', round(held, 6)
    if side:
        ok_qty = qty
        if side == 'buy':
            # هماهنگ‌کننده با واحد صحیح کار می‌کند؛ مقدار را به واحد یک‌هزارم بیت‌کوین می‌سنجیم
            cond = PC.Conductor(prules, acct, positions, os.path.join(STORE, 'portfolio', 'state.json'))
            units, _ = cond.approve(SLEEVE, {'symbol': 'BTCUSD', 'side': 'buy', 'qty': int(qty * 1000), 'price': price / 1000})
            ok_qty = round(units / 1000, 6)
        coid = f"btc-{dates[-1]}-{side}"
        if ok_qty > 0 and not DRY:
            r = requests.post(ENDPOINT + '/orders', headers=H, timeout=30,
                              json={'symbol': sym, 'qty': str(ok_qty), 'side': side, 'type': 'market',
                                    'time_in_force': 'gtc', 'client_order_id': coid})
            state['order'] = f'{r.status_code} {r.text[:200]}'
            ok = r.status_code in (200, 201) or 'client_order_id must be unique' in r.text
        else:
            ok = ok_qty > 0
        if ok and ok_qty > 0:
            act = 'خرید' if side == 'buy' else 'فروش'
            msg = (f"₿ <b>ربات مومنتوم بیت‌کوین — {act}</b>\n"
                   f"بازده ۲۸ روز گذشته: {roc * 100:+.1f}٪ ← {'مثبت: در بیت‌کوین می‌ماند' if sig else 'منفی: بیرون از بیت‌کوین'}\n"
                   f"{act} {ok_qty} بیت‌کوین به قیمت حدود {price:,.0f}$ (حدود {ok_qty * price:,.0f}$)"
                   + (' (آزمایشی، بدون سفارش)' if DRY else '') + "\n<i>حساب آزمایشی Alpaca</i>")
    os.makedirs(os.path.join(STORE, 'btc'), exist_ok=True)
    with open(os.path.join(STORE, 'btc', 'log.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(state, ensure_ascii=False) + '\n')
    print(json.dumps(state, ensure_ascii=False))
    if msg:
        print(msg)
        tg(msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
