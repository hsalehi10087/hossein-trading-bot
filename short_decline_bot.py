"""ربات «شورت افت»: سیگنال‌های آمریکای گزارش «افت قیمت» و «افت سود» را روی حساب آزمایشی شورت می‌کند.

داده: stock-scanner/data/losers_signal/today.json + data/fund_decline_signal/today.json — قواعد: rules/decline_short_bot.yaml
هر فروش (باز کردن شورت) و خرید (پوشش شورت) از هماهنگ‌کننده‌ی سبد (sleeve: decline_short) اجازه می‌گیرد.
فقط همین sleeve اجازه‌ی شورت دارد (rules/portfolio.yaml → short_allowed)؛ سقف شورت = capital_usd / short_margin_factor
تا این ربات هرگز بیش از سرمایه‌ی خودش به مارجین واقعی نیاز نداشته باشد و حساب کلی no_margin: true بماند.
وضعیت: datastore/decline_short/state.json {lots, closed, processed}  و  datastore/decline_short/owned.json

هر اجرا (روزهای کاری):
  ۰) تطبیق: قیمت و تعداد واقعی سفارش‌های قبلی از Alpaca خوانده می‌شود؛ سفارش اجرانشده حذف می‌شود
  ۱) پوشش (خروج): حد ضرر (قیمت بالا رفت)، بازگشت از کف (trailing)، یا پایان مدت نگهداری
  ۲) شورت تازه (ورود): سیگنال‌های تازه، هر سهم مبلغ ثابت؛ سهمی که ربات دیگری دارد شورت نمی‌شود
  ۳) پیام تلگرام فقط وقتی شورت یا پوششی شده
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from jalali import to_jalali_text as _jt  # noqa: E402
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
STATE = os.path.join(STORE, 'decline_short', 'state.json')
OWNED = os.path.join(STORE, 'decline_short', 'owned.json')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
DATA = 'https://data.alpaca.markets/v2'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
SLEEVE = 'decline_short'
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
DEAD = ('canceled', 'expired', 'rejected', 'done_for_day', 'stopped', 'suspended')
KIND_FA = {'losers': 'افت قیمت', 'fund_decline': 'افت سود'}


def fa(x):
    return str(x).translate(str.maketrans('0123456789.-', '۰۱۲۳۴۵۶۷۸۹٫−'))


def load_rules(path=os.path.join(ROOT, 'rules', 'decline_short_bot.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    need = ['version', 'stage', 'source_urls', 'market', 'capital_usd', 'short_margin_factor', 'trade_usd',
            'max_positions', 'signal_max_age_days', 'max_chase_pct', 'min_price_usd', 'stop_loss_pct', 'trail_pct',
            'max_hold_days', 'rebuy_cooldown_days', 'min_decline_streak']
    miss = [k for k in need if k not in r]
    if miss:
        raise ValueError(f'decline_short_bot rules missing {miss}')
    if r['stage'] != 'paper':
        raise ValueError('decline_short_bot: only paper stage is allowed')
    if not (0 < r['stop_loss_pct'] < 0.5 and 0 < r['trail_pct'] < 0.6 and 0 <= r['max_chase_pct'] <= 0.2
            and 0 < r['trade_usd'] <= r['capital_usd'] <= 50000 and 1 <= r['max_positions'] <= 50
            and 1.0 <= r['short_margin_factor'] <= 3.0
            and r['trade_usd'] * r['max_positions'] * r['short_margin_factor'] <= r['capital_usd'] * 1.001
            and r['max_hold_days'] >= 5):
        raise ValueError('decline_short_bot rules out of range')
    return r


def new_state():
    return {'lots': [], 'closed': [], 'processed': []}


# ---------------------------------------------------------------- منطق خالص (قابل آزمون)
def exit_reason(lot, price, rules, today):
    """دلیل پوشش یا None. trough پایین‌ترین قیمتی است که ربات بعد از شورت دیده (برعکس peak در ربات خرید)."""
    if not price:
        return None
    if price >= lot['entry'] * (1 + rules['stop_loss_pct']):
        return f"حد ضرر {rules['stop_loss_pct'] * 100:.0f}٪"
    trough = min(lot.get('trough') or lot['entry'], lot['entry'])
    if price >= trough * (1 + rules['trail_pct']):
        return f"بازگشت {rules['trail_pct'] * 100:.0f}٪ از کف"
    if (dt.date.fromisoformat(today) - dt.date.fromisoformat(lot['date'])).days >= rules['max_hold_days']:
        return f"پایان {rules['max_hold_days']} روز نگهداری"
    return None


def plan_entries(signals, state, rules, prices, today, taken=()):
    """سیگنال‌ها ← (فهرست شورت، فهرست ردشده با دلیل). هر سیگنال فیلدهای _sig_date و _kind را دارد.
    taken = نمادهایی که در حساب هستند یا صاحب دیگری دارند."""
    plans, skipped = [], []
    held = {l['ticker'] for l in state['lots']}
    cut = str(dt.date.fromisoformat(today) - dt.timedelta(days=rules['rebuy_cooldown_days']))
    recent = {c['ticker'] for c in state['closed'] if c['date_out'] >= cut}
    done = set(state['processed'])
    room = rules['max_positions'] - len(held)
    for s in signals:
        t = s.get('ticker')
        sig_date = s.get('_sig_date', '1970-01-01')
        sid = f"{s.get('_kind', '?')}:{sig_date}:{t}"
        age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(sig_date)).days
        p = prices.get(t)
        why = None
        if not t or sid in done:
            continue
        if age < 0 or age > rules['signal_max_age_days']:
            why = 'سیگنال کهنه'
        elif t in held:
            why = 'قبلاً شورت شده'
        elif t in taken:
            why = 'ربات دیگری این سهم را دارد'
        elif t in recent:
            why = 'تازه پوشش داده شده'
        elif s.get('_kind') == 'fund_decline' and (s.get('decline_streak') or 0) < rules['min_decline_streak']:
            why = f"کمتر از {rules['min_decline_streak']} فصل پیاپی افت سود"
        elif not p:
            why = 'قیمت نامعلوم'
        elif p < rules['min_price_usd']:
            why = 'قیمت خیلی پایین'
        elif s.get('price') and p < float(s['price']) * (1 - rules['max_chase_pct']):
            why = f"جاماندگی {p / float(s['price']) - 1:+.0%} (افت قبلاً اتفاق افتاده)"
        elif rules['trade_usd'] < p:
            why = 'قیمت یک سهم بیشتر از مبلغ هر شورت'
        elif room <= 0:
            why = 'سقف تعداد سهم پر است'
        if why:
            skipped.append((s, why))
            continue
        plans.append({'signal': s, 'sid': sid, 'ticker': t, 'qty': int(rules['trade_usd'] // p), 'price': p})
        held.add(t)
        room -= 1
    return plans, skipped


def reconcile(state, fetch_order):
    """قیمت و تعداد واقعی شورت‌های قبلی. سفارش مرده = حذف (و سیگنالش دوباره قابل بررسی نیست)."""
    keep, notes = [], []
    for lot in state['lots']:
        if lot.get('filled') or not lot.get('order_id') or lot['order_id'] == 'dry':
            keep.append(lot)
            continue
        o = fetch_order(lot['order_id'])
        if not o:
            keep.append(lot)
            continue
        fq = int(float(o.get('filled_qty') or 0))
        if o.get('status') == 'filled' or (o.get('status') in DEAD and fq >= 1):
            lot.update(qty=fq, entry=float(o['filled_avg_price']), trough=float(o['filled_avg_price']), filled=True)
            keep.append(lot)
        elif o.get('status') in DEAD:
            notes.append(f"{lot['ticker']}: سفارش شورت اجرا نشد ({o.get('status')})")
        else:
            keep.append(lot)
    state['lots'] = keep
    return notes


# ---------------------------------------------------------------- Alpaca
def get(path, base=ENDPOINT, **params):
    r = requests.get(base + path, headers=H, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_order(oid):
    try:
        return get(f'/orders/{oid}')
    except Exception as e:
        print('⚠️ order lookup', oid, e)
        return None


def order_by_coid(coid):
    try:
        r = requests.get(ENDPOINT + '/orders:by_client_order_id', headers=H, params={'client_order_id': coid}, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def latest_prices(tickers):
    out = {}
    tl = sorted(set(tickers))
    for i in range(0, len(tl), 100):
        try:
            js = get('/stocks/trades/latest', base=DATA, symbols=','.join(tl[i:i + 100]), feed='iex')
            for s, t in (js.get('trades') or {}).items():
                out[s] = float(t['p'])
        except Exception as e:
            print('⚠️ prices', e)
    return out


def order(symbol, qty, side, coid):
    if DRY:
        return {'id': 'dry', 'status': 'dry'}
    r = requests.post(ENDPOINT + '/orders', headers=H, timeout=30,
                      json={'symbol': symbol, 'qty': str(qty), 'side': side, 'type': 'market',
                            'time_in_force': 'day', 'client_order_id': coid[:48]})
    if r.status_code >= 300:
        print(f'❌ order {side} {symbol}: {r.status_code} {r.text[:150]}')
        return None
    return r.json()


def tg(text):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    print(text)
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage', timeout=30,
                      data={'chat_id': c, 'text': _jt(text[:3900]), 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'})


def save(state, prules):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, 'w'), ensure_ascii=False, indent=1)
    json.dump(sorted({l['ticker'] for l in state['lots']}), open(OWNED, 'w'))


def fetch_signals(rules):
    """سیگنال‌های هر دو منبع (افت قیمت + افت سود) را می‌خواند و با _sig_date/_kind برمی‌گرداند."""
    out = []
    for kind, url in rules['source_urls'].items():
        try:
            d = requests.get(url.rstrip('/') + '/today.json', timeout=30).json()
        except Exception as e:
            print('⚠️ fetch', kind, e)
            continue
        sig_date = d.get('date') or '1970-01-01'
        for s in (d.get('markets', {}).get(rules['market'], {}) or {}).get('signals', []) or []:
            out.append(dict(s, _sig_date=sig_date, _kind=kind))
    return out


# ---------------------------------------------------------------- اجرا
def main():
    rules, prules = load_rules(), PC.load()
    today = str(dt.date.today())
    clock = get('/clock')
    queued = False
    if not clock.get('is_open'):       # بازار بسته ولی جلسه‌ی بعدی نزدیک: سفارش در بازگشایی اجرا می‌شود
        try:
            nxt = dt.datetime.fromisoformat(clock['next_open'].replace('Z', '+00:00'))
            now = dt.datetime.fromisoformat(clock['timestamp'].replace('Z', '+00:00'))
            queued = (nxt - now).total_seconds() < 20 * 3600
        except Exception:
            queued = False
    if not (clock.get('is_open') or queued or DRY or '--force' in sys.argv):
        print('market closed and next session is far — no action')
        return 0

    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else new_state()
    notes = reconcile(state, fetch_order)
    for l in state['lots']:                       # سهم‌های خود ربات همیشه به نام خودش ثبت است
        PC.claim(prules, l['ticker'], SLEEVE)

    signals = fetch_signals(rules)

    acct, positions = get('/account'), get('/positions')
    pos = {p['symbol']: p for p in positions}
    cond = PC.Conductor(prules, acct, positions, os.path.join(STORE, 'portfolio', 'state.json'))
    prices = latest_prices({l['ticker'] for l in state['lots']} | {s['ticker'] for s in signals if s.get('ticker')})
    opens, covers = [], []

    # ۱) پوشش‌ها (خروج)
    keep = []
    for lot in state['lots']:
        t, p = lot['ticker'], prices.get(lot['ticker'])
        if lot.get('filled') and t not in pos:
            notes.append(f'{t}: در حساب نیست (دستی بسته شده؟) — از فهرست حذف شد')
            continue
        if p and lot.get('filled'):
            lot['trough'] = min(lot.get('trough') or lot['entry'], p)
        why = exit_reason(lot, p, rules, today) if lot.get('filled') else None
        if why:
            q = min(int(lot['qty']), int(abs(float(pos[t]['qty']))))
            if q >= 1 and cond.approve(SLEEVE, {'symbol': t, 'side': 'buy', 'qty': q, 'price': p})[0] \
                    and order(t, q, 'buy', f'ds-c-{t}-{today}'):
                ret = 1 - p / lot['entry']   # شورت: سود وقتی قیمت پایین‌تر از ورود پوشش داده شود
                state['closed'].append({'ticker': t, 'entry': lot['entry'], 'exit': p, 'ret': round(ret, 4),
                                        'date_in': lot['date'], 'date_out': today, 'reason': why, 'qty': q})
                covers.append((lot, p, ret, why))
                if not DRY:
                    continue
        keep.append(lot)
    state['lots'] = keep

    # ۲) شورت‌های تازه (ورود)
    own = {l['ticker'] for l in state['lots']}
    # بازیابی: اگر سفارش قبلی ثبت شده ولی وضعیت ذخیره نشده بود، همان سفارش به فهرست برمی‌گردد (شورت دوباره نمی‌شود)
    for sg in signals:
        t = sg.get('ticker')
        if t and t in pos and float(pos[t]['qty']) < 0 and t not in own \
                and f"{sg['_kind']}:{sg['_sig_date']}:{t}" not in state['processed']:
            o = order_by_coid(f"ds-o-{t}-{sg['_sig_date']}")
            if o and int(float(o.get('filled_qty') or 0)) >= 1 and PC.claim(prules, t, SLEEVE):
                px = float(o['filled_avg_price'])
                state['lots'].append({'ticker': t, 'name': sg.get('name', ''), 'qty': int(float(o['filled_qty'])),
                                      'entry': px, 'trough': px, 'date': (o.get('filled_at') or today)[:10],
                                      'signal_date': sg['_sig_date'], 'signal_price': sg.get('price'),
                                      'order_id': o.get('id'), 'filled': True})
                state['processed'].append(f"{sg['_kind']}:{sg['_sig_date']}:{t}")
                own.add(t)
                notes.append(f'{t}: شورت قبلی که ثبت نشده بود به فهرست برگشت')
    taken = {s for s in pos if s not in own}      # سهمی که در حساب هست و مال این ربات نیست (ربات دیگر)
    plans, skipped = plan_entries(signals, state, rules, prices, today, taken)
    for pl in plans:
        t, p = pl['ticker'], pl['price']
        if not PC.claim(prules, t, SLEEVE):
            skipped.append((pl['signal'], 'صاحب دیگری دارد'))
            continue
        q, checks = cond.approve(SLEEVE, {'symbol': t, 'side': 'sell', 'qty': pl['qty'], 'price': p})
        o = order(t, q, 'sell', f"ds-o-{t}-{pl['signal']['_sig_date']}") if q >= 1 else None
        if not o:
            prules['_dynamic'].pop(t, None)
            why = 'هماهنگ‌کننده: ' + ', '.join(c['name'].split(':')[-1] for c in checks if not c['passed']) if q < 1 \
                else 'سفارش پذیرفته نشد'
            skipped.append((pl['signal'], why))
            continue
        if not DRY:
            state['lots'].append({'ticker': t, 'name': pl['signal'].get('name', ''), 'qty': q, 'entry': p, 'trough': p,
                                  'date': today, 'signal_date': pl['signal']['_sig_date'],
                                  'signal_price': pl['signal'].get('price'), 'order_id': o.get('id'), 'filled': False})
            state['processed'].append(pl['sid'])
        opens.append((pl, q, p))
    state['processed'] = state['processed'][-5000:]

    if not DRY:
        save(state, prules)
    for s, why in skipped:
        print('skip', s.get('ticker'), why)

    if opens or covers or notes:
        L = ['<b>🔻 ربات شورت افت</b>' + (' (آزمایشی، بدون سفارش)' if DRY else '')]
        for pl, q, p in opens:
            kind_fa = KIND_FA.get(pl['signal'].get('_kind'), pl['signal'].get('_kind', ''))
            L.append(f"🔴 شورت <b>{pl['ticker']}</b> ({pl['signal'].get('name', '')}) × {fa(q)} به قیمت حدود "
                     f"{fa(f'{p:,.2f}')}$ — سیگنال {kind_fa} (حدود {fa(f'{q * p:,.0f}')}$)")
        for lot, p, ret, why in covers:
            L.append(f"🟢 پوشش <b>{lot['ticker']}</b>: {fa(f'{ret * 100:+.1f}')}٪ — {why}")
        short_notional = sum(l['qty'] * l['entry'] for l in state['lots'])
        L.append(f"<i>شورت‌های باز: {fa(len(state['lots']))} از {fa(rules['max_positions'])} | ارزش اسمی شورت: "
                 f"{fa(f'{short_notional:,.0f}')} از {fa(f'{rules['capital_usd']:,}')}$ "
                 f"(ضریب مارجین محافظه‌کارانه {rules['short_margin_factor']:g}×)</i>")
        L += [f'<i>⚠️ {n}</i>' for n in notes[:5]]
        if queued:
            L.append('<i>بازار بسته بود؛ سفارش‌ها در بازگشایی جلسه‌ی بعدی اجرا می‌شوند.</i>')
        L.append('<i>حساب آزمایشی Alpaca — شورت واقعی. خروج: حد ضرر، بازگشت از کف، یا پایان مدت نگهداری.</i>')
        tg('\n'.join(L))
    return 0


if __name__ == '__main__':
    sys.exit(main())
