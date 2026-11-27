"""«ربات آپشن»: اسپرد اختیار ریسک‌محدود (call و put، خرید و فروش) روی سهام/ETFهای نقدشونده.

هر روز: روند و مومنتوم از میله‌های روزانه ← انتخاب استراتژی ← پیدا کردن پاها از زنجیره‌ی واقعی Alpaca ←
دفتر آزمایشی (بدون سفارش واقعی) ← گزارش فارسی تلگرام: هر سیگنال به‌همراه دلیلش + وضعیت دفتر.
استراتژی‌ها:
  bull_put_credit   روند صعودی + اصلاح کوتاه (RSI ۴۰–۶۵): فروش اسپرد پوت (دریافت پریمیوم)
  bear_call_credit  روند نزولی + برگشت کوتاه (RSI ۳۵–۶۰): فروش اسپرد کال
  bull_call_debit   روند صعودی + مومنتوم قوی: خرید اسپرد کال
  bear_put_debit    روند نزولی + شکست قیمت: خرید اسپرد پوت
وضعیت: datastore/option/state.json {open, closed, signals, started}
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from jalali import to_jalali_text as _jt  # noqa: E402
import datetime as dt
import json
import os
import re
import sys

import requests
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.getenv('DATASTORE', 'datastore')
STATE = os.path.join(STORE, 'option', 'state.json')
DATA = 'https://data.alpaca.markets'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
NAMES = {'bull_put_credit': 'فروش اسپرد پوت (صعودی)', 'bear_call_credit': 'فروش اسپرد کال (نزولی)',
         'bull_call_debit': 'خرید اسپرد کال (صعودی)', 'bear_put_debit': 'خرید اسپرد پوت (نزولی)'}


def fa(x):
    return str(x).translate(str.maketrans('0123456789.-', '۰۱۲۳۴۵۶۷۸۹٫−'))


def load_rules(path=os.path.join(ROOT, 'rules', 'option_bot.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    if not str(r['stage']).startswith('paper'):
        raise ValueError('option_bot: only paper stage')
    if not (0 < r['risk_per_trade_usd'] <= r['capital_usd'] * 0.1 and 1 <= r['max_positions'] <= 20):
        raise ValueError('option_bot rules out of range')
    return r


# ---------------------------------------------------------------- منطق خالص
def sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def rsi(v, n=14):
    if len(v) < n + 1:
        return None
    g = [max(v[i] - v[i - 1], 0) for i in range(len(v) - n, len(v))]
    l = [max(v[i - 1] - v[i], 0) for i in range(len(v) - n, len(v))]
    ag, al = sum(g) / n, sum(l) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def pick_strategy(closes):
    """(استراتژی, دلیل فارسی) یا (None, دلیل ردشدن). فقط از قیمت‌های تا امروز استفاده می‌کند."""
    if len(closes) < 205:
        return None, 'تاریخچه کافی نیست'
    c = closes[-1]
    s50, s200, r = sma(closes, 50), sma(closes, 200), rsi(closes)
    ret20 = c / closes[-21] - 1
    hi20 = max(closes[-20:])
    up = c > s50 > s200
    down = c < s50 < s200
    info = f'قیمت {c:.2f}، میانگین۵۰ {s50:.2f}، میانگین۲۰۰ {s200:.2f}، RSI {r:.0f}، ۲۰روزه {ret20 * 100:+.1f}٪'
    if up and r > 60 and ret20 > 0.05 and c >= hi20 * 0.98:
        return 'bull_call_debit', 'روند صعودی + مومنتوم قوی نزدیک سقف ۲۰روزه — ' + info
    if up and 40 <= r <= 65:
        return 'bull_put_credit', 'روند صعودی و اصلاح کوتاه؛ فروش پوت دورتر از قیمت برای دریافت پریمیوم — ' + info
    if down and r < 40 and ret20 < -0.05 and c <= min(closes[-20:]) * 1.02:
        return 'bear_put_debit', 'روند نزولی + شکست قیمت نزدیک کف ۲۰روزه — ' + info
    if down and 35 <= r <= 60:
        return 'bear_call_credit', 'روند نزولی و برگشت کوتاه؛ فروش کال دورتر از قیمت — ' + info
    return None, 'سیگنال واضح نیست — ' + info


OCC = re.compile(r'^([A-Z]{1,6})(\d{6})([CP])(\d{8})$')


def parse_occ(sym):
    m = OCC.match(sym)
    if not m:
        return None
    return {'root': m.group(1), 'expiry': dt.datetime.strptime(m.group(2), '%y%m%d').date(),
            'type': m.group(3), 'strike': int(m.group(4)) / 1000}


def mid(q):
    b, a = q.get('bp') or 0, q.get('ap') or 0
    return (a + b) / 2 if a > 0 and b > 0 else None


def liquid(snap, rules):
    q = snap.get('latestQuote') or {}
    m = mid(q)
    return m is not None and (q['ap'] - q['bp']) / m <= rules['max_rel_spread']


def build_spread(strategy, chain, today, rules):
    """chain: {occ: snapshot(dict با latestQuote, greeks.delta)}. خروجی: دیکشنری معامله یا None."""
    kind = 'P' if strategy in ('bull_put_credit', 'bear_put_debit') else 'C'
    credit = strategy.endswith('credit')
    rows = []
    for occ, sn in chain.items():
        o = parse_occ(occ)
        d = (sn.get('greeks') or {}).get('delta')
        if not o or o['type'] != kind or d is None or not liquid(sn, rules):
            continue
        dte = (o['expiry'] - today).days
        if rules['min_dte'] <= dte <= rules['max_dte']:
            rows.append((o, abs(float(d)), mid(sn['latestQuote']), occ, dte))
    if not rows:
        return None
    exp = min({r[0]['expiry'] for r in rows}, key=lambda e: abs((e - today).days - 36))
    rows = [r for r in rows if r[0]['expiry'] == exp]
    tgt_short = rules['short_delta'] if credit else rules['short_delta_debit']
    tgt_a = tgt_short if credit else rules['long_delta_debit']
    near = lambda t: min(rows, key=lambda r: abs(r[1] - t))
    a = near(tgt_a)                                   # پای اصلی: فروخته (اعتباری) یا خریده (بدهکار)
    b_t = tgt_short if not credit else None
    if credit:                                        # پای محافظ: خریده‌شده دورتر از پول
        cand = [r for r in rows if (r[0]['strike'] < a[0]['strike'] if kind == 'P' else r[0]['strike'] > a[0]['strike'])]
        if not cand:
            return None
        width_t = max(1.0, a[0]['strike'] * 0.03)
        b = min(cand, key=lambda r: abs(abs(r[0]['strike'] - a[0]['strike']) - width_t))
    else:                                             # پای فروخته‌شده دورتر برای کاهش هزینه
        b = near(b_t)
        if b[0]['strike'] == a[0]['strike']:
            return None
    width = abs(a[0]['strike'] - b[0]['strike'])
    if width <= 0:
        return None
    if credit:
        net = a[2] - b[2]                             # اعتبار دریافتی هر سهم
        if net <= 0 or net < rules['credit_min_frac'] * width:
            return None
        max_loss, max_gain = width - net, net
    else:
        net = a[2] - b[2]                             # هزینه‌ی پرداختی
        if net <= 0 or net > rules['debit_max_frac'] * width or (kind == 'P') != (a[0]['strike'] > b[0]['strike']):
            return None
        max_loss, max_gain = net, width - net
    return {'strategy': strategy, 'expiry': str(exp), 'dte': a[4], 'kind': kind, 'width': round(width, 2),
            'leg_a': a[3], 'leg_b': b[3], 'sell_a': credit, 'entry_net': round(net, 3),
            'max_loss': round(max_loss, 3), 'max_gain': round(max_gain, 3), 'delta_a': round(a[1], 2)}


def spread_value(pos, quotes):
    """ارزش فعلی اسپرد هر سهم (هزینه‌ی بستن برای اعتباری، ارزش فروش برای بدهکار) از قیمت میانه."""
    ma, mb = mid(quotes.get(pos['leg_a']) or {}), mid(quotes.get(pos['leg_b']) or {})
    return None if ma is None or mb is None else round(ma - mb, 3)


def exit_reason(pos, val, today, rules):
    if val is None:
        return None
    n = pos['entry_net']
    if pos['sell_a']:                                 # اعتباری: val = هزینه‌ی بستن
        if val <= n * (1 - rules['credit_take_profit']):
            return 'سود ۵۰٪ حداکثر'
        if val >= n * rules['credit_stop_mult']:
            return 'حد ضرر (هزینه‌ی بستن ۲ برابر اعتبار)'
    else:                                             # بدهکار: val = ارزش فروش
        if val >= n * (1 + rules['debit_take_profit']):
            return 'سود ۶۰٪'
        if val <= n * (1 - rules['debit_stop']):
            return 'حد ضرر ۵۰٪'
    if (dt.date.fromisoformat(pos['expiry']) - today).days <= rules['exit_dte']:
        return 'نزدیک سررسید (≤۷ روز)'
    return None


def pnl_per_share(pos, val):
    return (pos['entry_net'] - val) if pos['sell_a'] else (val - pos['entry_net'])


def qty_for(spread, rules):
    return int(rules['risk_per_trade_usd'] // (spread['max_loss'] * 100))


# ---------------------------------------------------------------- Alpaca
def get(path, **params):
    r = requests.get(DATA + path, headers=H, params=params, timeout=40)
    r.raise_for_status()
    return r.json()


def daily_closes(sym, n=260):
    start = str(dt.date.today() - dt.timedelta(days=int(n * 1.6)))
    js = get('/v2/stocks/bars', symbols=sym, timeframe='1Day', start=start, limit=1000, feed='iex', adjustment='split')
    return [float(b['c']) for b in (js.get('bars') or {}).get(sym, [])]


def option_chain(sym, kind, today, rules):
    lo = str(today + dt.timedelta(days=rules['min_dte'] - 2))
    hi = str(today + dt.timedelta(days=rules['max_dte'] + 2))
    out, tok = {}, None
    for _ in range(6):
        p = {'feed': 'indicative', 'type': 'put' if kind == 'P' else 'call', 'expiration_date_gte': lo,
             'expiration_date_lte': hi, 'limit': 1000}
        if tok:
            p['page_token'] = tok
        js = get(f'/v1beta1/options/snapshots/{sym}', **p)
        out.update(js.get('snapshots') or {})
        tok = js.get('next_page_token')
        if not tok:
            break
    return out


def quotes_for(syms):
    out = {}
    syms = sorted(set(syms))
    for i in range(0, len(syms), 100):
        js = get('/v1beta1/options/snapshots', symbols=','.join(syms[i:i + 100]), feed='indicative')
        for s, sn in (js.get('snapshots') or {}).items():
            out[s] = sn.get('latestQuote') or {}
    return out


def tg(text):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    print(text)
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage', timeout=30,
                      data={'chat_id': c, 'text': _jt(text[:3900]), 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'})


def summary(state):
    cl = state['closed']
    if not cl:
        return None
    pl = [c['pnl_usd'] for c in cl]
    return {'n': len(cl), 'win': sum(p > 0 for p in pl) / len(cl) * 100, 'pnl': sum(pl),
            'risk': sum(c['risk_usd'] for c in cl)}


def main():
    rules = load_rules()
    today = dt.date.today()
    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else \
        {'open': [], 'closed': [], 'signals': [], 'started': str(today)}
    lines = []

    # ۱) مدیریت پوزیشن‌های باز (دفتر آزمایشی)
    q = quotes_for([s for p in state['open'] for s in (p['leg_a'], p['leg_b'])]) if state['open'] else {}
    keep = []
    for p in state['open']:
        val = spread_value(p, q)
        why = exit_reason(p, val, today, rules)
        if why:
            pnl = pnl_per_share(p, val) * 100 * p['qty']
            state['closed'].append({**p, 'exit': val, 'date_out': str(today), 'reason': why,
                                    'pnl_usd': round(pnl, 2), 'risk_usd': round(p['max_loss'] * 100 * p['qty'], 2)})
            lines.append(f"🔒 بسته شد {p['underlying']} — {NAMES[p['strategy']]}: {fa(round(pnl, 1))}$ ({why})")
        else:
            p['last_val'] = val
            keep.append(p)
    state['open'] = keep

    # ۲) سیگنال‌های تازه
    new = []
    held = {p['underlying'] for p in state['open']}
    for sym in rules['universe']:
        if len(state['open']) + len(new) >= rules['max_positions'] or len(new) >= rules['max_new_per_day']:
            break
        if sym in held:
            continue
        try:
            strat, why = pick_strategy(daily_closes(sym))
            if not strat:
                continue
            kind = 'P' if strat in ('bull_put_credit', 'bear_put_debit') else 'C'
            sp = build_spread(strat, option_chain(sym, kind, today, rules), today, rules)
            if not sp:
                print('skip', sym, strat, 'no suitable spread')
                continue
        except Exception as e:
            print('⚠️', sym, e)
            continue
        qty = qty_for(sp, rules)
        if qty < 1:
            continue
        pos = {**sp, 'underlying': sym, 'qty': qty, 'date_in': str(today), 'why': why}
        new.append(pos)
        state['signals'].append({'date': str(today), 'underlying': sym, 'strategy': strat, 'why': why,
                                 'legs': f"{sp['leg_a']} / {sp['leg_b']}", 'net': sp['entry_net']})
    state['open'] += new

    # ۳) گزارش
    L = ['<b>🎯 ربات آپشن — گزارش روزانه</b> (دفتر آزمایشی، سفارش واقعی ثبت نشده)']
    for p in new:
        side = 'دریافتی' if p['sell_a'] else 'پرداختی'
        L.append(f"\n🆕 <b>{p['underlying']}</b> — {NAMES[p['strategy']]}\n"
                 f"دلیل: {p['why']}\n"
                 f"{p['leg_a']} ↔ {p['leg_b']} | سررسید {p['expiry']} ({fa(p['dte'])} روز)\n"
                 f"{side} هر سهم: {fa(p['entry_net'])}$ | تعداد: {fa(p['qty'])} | بیشترین ضرر: "
                 f"{fa(round(p['max_loss'] * 100 * p['qty']))}$ | بیشترین سود: {fa(round(p['max_gain'] * 100 * p['qty']))}$")
    if not new:
        L.append('امروز سیگنال تازه‌ای با شرایط نقدشوندگی و قیمت مناسب پیدا نشد.')
    L += [''] + lines
    if state['open']:
        L.append('\n<b>پوزیشن‌های باز:</b>')
        for p in state['open']:
            v = p.get('last_val')
            pl = f" | سود/زیان فعلی {fa(round(pnl_per_share(p, v) * 100 * p['qty'], 1))}$" if v is not None else ''
            L.append(f"• {p['underlying']} {NAMES[p['strategy']]} (از {p['date_in']}){pl}")
    s = summary(state)
    days = (today - dt.date.fromisoformat(state['started'])).days
    if s:
        L.append(f"\n<b>جمع بسته‌شده‌ها:</b> {fa(s['n'])} معامله، برد {fa(round(s['win']))}٪، سود/زیان {fa(round(s['pnl'], 1))}$")
    if days >= 7 and not state.get('week_reported'):
        L.append('\n📊 <b>گزارش یک‌هفته:</b> ' + (f"{fa(s['n'])} معامله بسته شد؛ سود/زیان {fa(round(s['pnl'], 1))}$." if s else
                 'تا امروز معامله‌ای بسته نشده؛ بازده فقط روی پوزیشن‌های باز است.'))
        state['week_reported'] = True
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    tg('\n'.join(L))
    return 0


if __name__ == '__main__':
    sys.exit(main())
