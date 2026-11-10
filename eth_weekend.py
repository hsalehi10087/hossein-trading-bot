"""ربات «آخر هفته‌ی اتریوم» (حساب آزمایشی Alpaca، سقف ۵۰۰۰ دلار).

قاعده (همان روشی که در شکارچی روش قبول شد):
  ورود: بسته‌شدن کندل روزانه‌ی جمعه (UTC) ≥ ۰٫۹۸۵ × EMA55 و RSI14 بین ۳۸ و ۷۲ ← خرید شنبه ۰۰:۰۰ UTC
  خروج: حد ضرر ۴٫۵٪ (سفارش حد ضرر نزد بروکر)، حد سود ۷٫۵٪، اگر کندل شنبه با RSI بالای ۷۶ بست ← یکشنبه،
        وگرنه دوشنبه ۰۰:۰۰ UTC (بعد از بسته‌شدن کندل یکشنبه).
هر اجرا بی‌خطر تکرارپذیر است: وضعیت در datastore/eth/state.json؛ خرید از هماهنگ‌کننده‌ی سبد اجازه می‌گیرد.
پیام تلگرام فقط هنگام ورود و خروج.
"""
import datetime as dt
import json
import os
import sys
import time

import pandas as pd
import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import conductor as PC  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.getenv('DATASTORE', 'datastore')
STATE = os.path.join(STORE, 'eth', 'state.json')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
DATA = 'https://data.alpaca.markets/v1beta3/crypto/us'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
SLEEVE = 'eth_weekend'


def load_rules(path=os.path.join(ROOT, 'rules', 'eth_weekend.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    e, x = r['entry'], r['exit']
    if not (0 < r['capital_usd'] <= 50000 and 0 < x['stop_loss_pct'] < 0.2 and 0 < x['take_profit_pct'] < 0.5
            and 0 < e['rsi_min'] < e['rsi_max'] < 100):
        raise ValueError('eth rules out of range')
    return r


# ---------------- منطق خالص (قابل آزمون) ----------------
def indicators(closes, rules):
    s = pd.Series(closes, dtype=float)
    e = rules['entry']
    ema = s.ewm(span=e['ema_n'], adjust=False).mean()
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / e['rsi_n'], adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / e['rsi_n'], adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    return float(s.iloc[-1]), float(ema.iloc[-1]), float(rsi.iloc[-1])


def entry_ok(close, ema, rsi, rules):
    e = rules['entry']
    return close >= ema * e['ema_tolerance'] and e['rsi_min'] <= rsi <= e['rsi_max']


def decide(now, last_bar_date, close, ema, rsi, pos, price, rules):
    """now: زمان UTC؛ last_bar_date: تاریخ آخرین کندل بسته‌شده؛ pos: None یا {'entry': قیمت ورود}.
    خروجی: ('buy'|'sell'|None, دلیل)."""
    x = rules['exit']
    wd_bar = last_bar_date.weekday()             # ۴ جمعه، ۵ شنبه، ۶ یکشنبه
    if pos is None:
        if wd_bar == 4 and now.weekday() == 5 and now.hour < rules['entry']['latest_entry_hour_utc'] \
                and entry_ok(close, ema, rsi, rules):
            return 'buy', 'ورود آخر هفته'
        return None, ''
    if price <= pos['entry'] * (1 - x['stop_loss_pct']):
        return 'sell', 'حد ضرر ۴٫۵٪'
    if price >= pos['entry'] * (1 + x['take_profit_pct']):
        return 'sell', 'حد سود ۷٫۵٪'
    if wd_bar == 5 and rsi > x['rsi_exit']:
        return 'sell', 'RSI بالای ۷۶ (یکشنبه)'
    if now.weekday() not in (5, 6):              # دوشنبه یا دیرتر: پایان آخر هفته
        return 'sell', 'پایان آخر هفته'
    return None, ''


# ---------------- Alpaca ----------------
def api(method, path, **kw):
    r = requests.request(method, ENDPOINT + path, headers=H, timeout=30, **kw)
    return r


def daily_closes(symbol, days=150):
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime('%Y-%m-%dT00:00:00Z')
    r = requests.get(f'{DATA}/bars', headers=H, timeout=30,
                     params={'symbols': symbol, 'timeframe': '1Day', 'start': start, 'limit': 1000})
    r.raise_for_status()
    today = dt.datetime.now(dt.timezone.utc).date()
    bars = [b for b in r.json()['bars'][symbol] if dt.date.fromisoformat(b['t'][:10]) < today]
    return dt.date.fromisoformat(bars[-1]['t'][:10]), [float(b['c']) for b in bars]


def last_price(symbol):
    r = requests.get(f'{DATA}/latest/trades', headers=H, timeout=30, params={'symbols': symbol})
    r.raise_for_status()
    return float(r.json()['trades'][symbol]['p'])


def tg(text):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage', timeout=20,
                      data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'})


def main():
    rules, prules = load_rules(), PC.load()
    sym = rules['symbol']
    now = dt.datetime.now(dt.timezone.utc)
    st = json.load(open(STATE)) if os.path.exists(STATE) else {'pos': None, 'history': []}
    bar_date, closes = daily_closes(sym)
    close, ema, rsi = indicators(closes, rules)
    price = last_price(sym)
    positions = api('GET', '/positions').json()
    held = sum(float(p['qty']) for p in positions if p['symbol'] in ('ETHUSD', 'ETH/USD'))
    pos = st['pos']
    notes = []
    # اگر حد ضرر نزد بروکر پر شده، موقعیت بسته است
    if pos and held * price < 20:
        o = api('GET', f"/orders/{pos.get('stop_id')}").json() if pos.get('stop_id') else {}
        fill = float(o.get('filled_avg_price') or pos['entry'] * (1 - rules['exit']['stop_loss_pct']))
        st['history'].append({'in': pos['date'], 'out': now.date().isoformat(), 'entry': pos['entry'], 'exit': fill,
                              'ret_pct': round((fill / pos['entry'] - 1) * 100, 2), 'why': 'حد ضرر (نزد بروکر)'})
        tg(f"⟠ <b>اتریوم آخر هفته — فروش با حد ضرر</b>\nورود {pos['entry']:,.2f}$ ← خروج {fill:,.2f}$ "
           f"({(fill / pos['entry'] - 1) * 100:+.2f}٪)\n<i>حساب آزمایشی Alpaca</i>")
        pos = st['pos'] = None
    act, why = decide(now, bar_date, close, ema, rsi, pos, price, rules)
    log = {'ts': now.isoformat(timespec='minutes'), 'bar': str(bar_date), 'close': close, 'ema55': round(ema, 2),
           'rsi14': round(rsi, 1), 'price': price, 'pos': bool(pos), 'action': act, 'why': why}
    if act == 'buy':
        qty = round(rules['capital_usd'] / price, 4)
        cond = PC.Conductor(prules, api('GET', '/account').json(), positions, os.path.join(STORE, 'portfolio', 'state.json'))
        units, _ = cond.approve(SLEEVE, {'symbol': 'ETHUSD', 'side': 'buy', 'qty': int(qty * 1000), 'price': price / 1000})
        qty = round(units / 1000, 3)
        if qty > 0 and not DRY:
            r = api('POST', '/orders', json={'symbol': sym, 'qty': str(qty), 'side': 'buy', 'type': 'market',
                                             'time_in_force': 'gtc', 'client_order_id': f'eth-buy-{now.date()}'})
            if r.status_code not in (200, 201):
                notes.append(f'buy failed {r.status_code} {r.text[:150]}')
                qty = 0
            else:
                time.sleep(4)
                o = api('GET', f"/orders/{r.json()['id']}").json()
                fill = float(o.get('filled_avg_price') or price)
                qty = float(o.get('filled_qty') or qty)
                stop = round(fill * (1 - rules['exit']['stop_loss_pct']), 2)
                rs = api('POST', '/orders', json={'symbol': sym, 'qty': str(qty), 'side': 'sell', 'type': 'stop_limit',
                                                  'stop_price': str(stop), 'limit_price': str(round(stop * 0.995, 2)),
                                                  'time_in_force': 'gtc'})
                st['pos'] = {'date': now.date().isoformat(), 'entry': fill, 'qty': qty,
                             'stop_id': rs.json().get('id') if rs.status_code in (200, 201) else None}
                if not st['pos']['stop_id']:
                    notes.append(f'stop order failed {rs.status_code} {rs.text[:150]}')
                tg(f"⟠ <b>اتریوم آخر هفته — خرید</b>\n{qty} اتریوم به قیمت {fill:,.2f}$ (حدود {qty * fill:,.0f}$)\n"
                   f"شرط ورود: قیمت {close:,.0f} در برابر EMA55 {ema:,.0f} | RSI14 {rsi:.0f}\n"
                   f"حد ضرر {stop:,.2f}$ (ثبت نزد بروکر) | حد سود {fill * (1 + rules['exit']['take_profit_pct']):,.2f}$ | "
                   f"خروج حداکثر دوشنبه ۰۳:۳۰ تهران\n<i>حساب آزمایشی Alpaca</i>")
        elif DRY:
            notes.append(f'DRY: would buy {qty}')
    elif act == 'sell' and pos:
        if not DRY:
            if pos.get('stop_id'):
                api('DELETE', f"/orders/{pos['stop_id']}")
                time.sleep(2)
            r = api('POST', '/orders', json={'symbol': sym, 'qty': str(round(held, 6)), 'side': 'sell', 'type': 'market',
                                             'time_in_force': 'gtc'})
            ok = r.status_code in (200, 201)
            fill = price
            if ok:
                time.sleep(4)
                o = api('GET', f"/orders/{r.json()['id']}").json()
                fill = float(o.get('filled_avg_price') or price)
            else:
                notes.append(f'sell failed {r.status_code} {r.text[:150]}')
        else:
            ok, fill = True, price
        if ok:
            ret = (fill / pos['entry'] - 1) * 100
            st['history'].append({'in': pos['date'], 'out': now.date().isoformat(), 'entry': pos['entry'], 'exit': fill,
                                  'ret_pct': round(ret, 2), 'why': why})
            st['pos'] = None
            h = st['history']
            tot = 1.0
            for x in h:
                tot *= 1 + x['ret_pct'] / 100
            tg(f"⟠ <b>اتریوم آخر هفته — فروش ({why})</b>\nورود {pos['entry']:,.2f}$ ← خروج {fill:,.2f}$ "
               f"(<b>{ret:+.2f}٪</b>، حدود {ret / 100 * pos['entry'] * pos['qty']:+,.0f}$)\n"
               f"کارنامه: {len(h)} آخر هفته، {sum(1 for x in h if x['ret_pct'] > 0)} سودده، بازده جمع {(tot - 1) * 100:+.1f}٪"
               + (' (آزمایشی، بدون سفارش)' if DRY else '') + "\n<i>حساب آزمایشی Alpaca</i>")
    log['notes'] = notes
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    if not DRY:
        json.dump(st, open(STATE, 'w'), ensure_ascii=False, indent=1)
    with open(os.path.join(STORE, 'eth', 'log.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(log, ensure_ascii=False) + '\n')
    print(json.dumps(log, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
