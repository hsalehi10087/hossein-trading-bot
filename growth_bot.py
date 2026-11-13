"""ربات «سیگنال رشد»: سیگنال‌های آمریکای گزارش «احتمال رشد تکنیکال و بنیادی» را روی حساب آزمایشی می‌خرد.

داده: stock-scanner/data/growth_signal/today.json  — قواعد: rules/growth_bot.yaml
هر خرید و فروش از هماهنگ‌کننده‌ی سبد (sleeve: growth_signal) اجازه می‌گیرد.
وضعیت: datastore/growth/state.json {lots, closed, processed}  و  datastore/growth/owned.json (فهرست سهم‌های ربات)

هر اجرا (روزهای کاری):
  ۰) تطبیق: قیمت و تعداد واقعی سفارش‌های قبلی از Alpaca خوانده می‌شود؛ سفارش اجرانشده حذف می‌شود
  ۱) خروج: حد ضرر، افت از سقف، یا پایان مدت نگهداری
  ۲) ورود: سیگنال‌های تازه، هر سهم مبلغ ثابت؛ سهمی که ربات دیگری دارد خریده نمی‌شود
  ۳) پیام تلگرام فقط وقتی خرید یا فروشی شده
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
STATE = os.path.join(STORE, 'growth', 'state.json')
OWNED = os.path.join(STORE, 'growth', 'owned.json')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
DATA = 'https://data.alpaca.markets/v2'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
SLEEVE = 'growth_signal'
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
DEAD = ('canceled', 'expired', 'rejected', 'done_for_day', 'stopped', 'suspended')


def fa(x):
    return str(x).translate(str.maketrans('0123456789.-', '۰۱۲۳۴۵۶۷۸۹٫−'))


def load_rules(path=os.path.join(ROOT, 'rules', 'growth_bot.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    need = ['version', 'stage', 'source_url', 'market', 'capital_usd', 'trade_usd', 'max_positions',
            'signal_max_age_days', 'max_chase_pct', 'min_price_usd', 'stop_loss_pct', 'trail_pct',
            'max_hold_days', 'rebuy_cooldown_days', 'min_profit_streak']
    miss = [k for k in need if k not in r]
    if miss:
        raise ValueError(f'growth_bot rules missing {miss}')
    if r['stage'] != 'paper':
        raise ValueError('growth_bot: only paper stage is allowed')
    if not (0 < r['stop_loss_pct'] < 0.5 and 0 < r['trail_pct'] < 0.6 and 0 <= r['max_chase_pct'] <= 0.2
            and 0 < r['trade_usd'] <= r['capital_usd'] <= 50000 and 1 <= r['max_positions'] <= 50
            and r['trade_usd'] * r['max_positions'] <= r['capital_usd'] * 1.001 and r['max_hold_days'] >= 5):
        raise ValueError('growth_bot rules out of range')
    return r


def new_state():
    return {'lots': [], 'closed': [], 'processed': []}


# ---------------------------------------------------------------- منطق خالص (قابل آزمون)
def exit_reason(lot, price, rules, today):
    """دلیل فروش یا None. peak بالاترین قیمتی است که ربات بعد از خرید دیده."""
    if not price:
        return None
    if price <= lot['entry'] * (1 - rules['stop_loss_pct']):
        return f"حد ضرر {rules['stop_loss_pct'] * 100:.0f}٪"
    peak = max(lot.get('peak') or lot['entry'], lot['entry'])
    if price <= peak * (1 - rules['trail_pct']):
        return f"افت {rules['trail_pct'] * 100:.0f}٪ از سقف"
    if (dt.date.fromisoformat(today) - dt.date.fromisoformat(lot['date'])).days >= rules['max_hold_days']:
        return f"پایان {rules['max_hold_days']} روز نگهداری"
    return None


def plan_entries(sig_date, signals, state, rules, prices, today, taken=()):
    """سیگنال‌ها ← (فهرست خرید، فهرست ردشده با دلیل). taken = نمادهایی که در حساب هستند یا صاحب دیگری دارند."""
    plans, skipped = [], []
    age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(sig_date)).days
    held = {l['ticker'] for l in state['lots']}
    cut = str(dt.date.fromisoformat(today) - dt.timedelta(days=rules['rebuy_cooldown_days']))
    recent = {c['ticker'] for c in state['closed'] if c['date_out'] >= cut}
    done = set(state['processed'])
    room = rules['max_positions'] - len(held)
    for s in signals:
        t = s.get('ticker')
        sid = f'{sig_date}:{t}'
        p = prices.get(t)
        why = None
        if not t or sid in done:
            continue
        if age < 0 or age > rules['signal_max_age_days']:
            why = 'سیگنال کهنه'
        elif t in held:
            why = 'قبلاً خریده شده'
        elif t in taken:
            why = 'ربات دیگری این سهم را دارد'
        elif t in recent:
            why = 'تازه فروخته شده'
        elif (s.get('profit_streak') or 0) < rules['min_profit_streak']:
            why = f"سود کمتر از {rules['min_profit_streak']} فصل پیاپی بالا رفته"
        elif not p:
            why = 'قیمت نامعلوم'
        elif p < rules['min_price_usd']:
            why = 'قیمت خیلی پایین'
        elif s.get('price') and p > float(s['price']) * (1 + rules['max_chase_pct']):
            why = f"جاماندگی {p / float(s['price']) - 1:+.0%}"
        elif rules['trade_usd'] < p:
            why = 'قیمت یک سهم بیشتر از مبلغ هر خرید'
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
    """قیمت و تعداد واقعی خریدهای قبلی. سفارش مرده = حذف (و سیگنالش دوباره قابل بررسی نیست)."""
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
            lot.update(qty=fq, entry=float(o['filled_avg_price']), peak=float(o['filled_avg_price']), filled=True)
            keep.append(lot)
        elif o.get('status') in DEAD:
            notes.append(f"{lot['ticker']}: سفارش خرید اجرا نشد ({o.get('status')})")
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
                      data={'chat_id': c, 'text': text[:3900], 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'})


def save(state, prules):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, 'w'), ensure_ascii=False, indent=1)
    json.dump(sorted({l['ticker'] for l in state['lots']}), open(OWNED, 'w'))


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

    d = requests.get(rules['source_url'].rstrip('/') + '/today.json', timeout=30).json()
    sig_date = d.get('date') or '1970-01-01'
    signals = (d.get('markets', {}).get(rules['market'], {}) or {}).get('signals', []) or []

    acct, positions = get('/account'), get('/positions')
    pos = {p['symbol']: p for p in positions}
    cond = PC.Conductor(prules, acct, positions, os.path.join(STORE, 'portfolio', 'state.json'))
    prices = latest_prices({l['ticker'] for l in state['lots']} | {s['ticker'] for s in signals if s.get('ticker')} | {'SPY'})
    spy = prices.get('SPY')
    buys, sells = [], []

    # ۱) خروج‌ها
    keep = []
    for lot in state['lots']:
        t, p = lot['ticker'], prices.get(lot['ticker'])
        if lot.get('filled') and t not in pos:
            notes.append(f'{t}: در حساب نیست (دستی بسته شده؟) — از فهرست حذف شد')
            continue
        if p and lot.get('filled'):
            lot['peak'] = max(lot.get('peak') or lot['entry'], p)
        why = exit_reason(lot, p, rules, today) if lot.get('filled') else None
        if why:
            q = min(int(lot['qty']), int(float(pos[t]['qty'])))
            if q >= 1 and cond.approve(SLEEVE, {'symbol': t, 'side': 'sell', 'qty': q, 'price': p})[0] \
                    and order(t, q, 'sell', f'gs-s-{t}-{today}'):
                ret = p / lot['entry'] - 1
                state['closed'].append({'ticker': t, 'entry': lot['entry'], 'exit': p, 'ret': round(ret, 4),
                                        'spy_ret': round(spy / lot['spy_entry'] - 1, 4) if spy and lot.get('spy_entry') else None,
                                        'date_in': lot['date'], 'date_out': today, 'reason': why, 'qty': q})
                sells.append((lot, p, ret, why))
                if not DRY:
                    continue
        keep.append(lot)
    state['lots'] = keep

    # ۲) ورودها
    own = {l['ticker'] for l in state['lots']}
    # بازیابی: اگر سفارش قبلی ثبت شده ولی وضعیت ذخیره نشده بود، همان سفارش به فهرست برمی‌گردد (خرید دوباره نمی‌شود)
    for sg in signals:
        t = sg.get('ticker')
        if t and t in pos and t not in own and f'{sig_date}:{t}' not in state['processed']:
            o = order_by_coid(f'gs-b-{t}-{sig_date}')
            if o and int(float(o.get('filled_qty') or 0)) >= 1 and PC.claim(prules, t, SLEEVE):
                px = float(o['filled_avg_price'])
                state['lots'].append({'ticker': t, 'name': sg.get('name', ''), 'qty': int(float(o['filled_qty'])),
                                      'entry': px, 'peak': px, 'date': (o.get('filled_at') or today)[:10],
                                      'signal_date': sig_date, 'signal_price': sg.get('price'), 'spy_entry': spy,
                                      'order_id': o.get('id'), 'filled': True})
                state['processed'].append(f'{sig_date}:{t}')
                own.add(t)
                notes.append(f'{t}: خرید قبلی که ثبت نشده بود به فهرست برگشت')
    taken = {s for s in pos if s not in own}      # سهمی که در حساب هست و مال این ربات نیست (ربات دیگر)
    plans, skipped = plan_entries(sig_date, signals, state, rules, prices, today, taken)
    for pl in plans:
        t, p = pl['ticker'], pl['price']
        if not PC.claim(prules, t, SLEEVE):
            skipped.append((pl['signal'], 'صاحب دیگری دارد'))
            continue
        q, checks = cond.approve(SLEEVE, {'symbol': t, 'side': 'buy', 'qty': pl['qty'], 'price': p})
        o = order(t, q, 'buy', f'gs-b-{t}-{sig_date}') if q >= 1 else None
        if not o:
            prules['_dynamic'].pop(t, None)
            why = 'هماهنگ‌کننده: ' + ', '.join(c['name'].split(':')[-1] for c in checks if not c['passed']) if q < 1 \
                else 'سفارش پذیرفته نشد'
            skipped.append((pl['signal'], why))
            continue
        if not DRY:
            state['lots'].append({'ticker': t, 'name': pl['signal'].get('name', ''), 'qty': q, 'entry': p, 'peak': p,
                                  'date': today, 'signal_date': sig_date, 'signal_price': pl['signal'].get('price'),
                                  'spy_entry': spy, 'order_id': o.get('id'), 'filled': False})
            state['processed'].append(pl['sid'])
        buys.append((pl, q, p))
    state['processed'] = state['processed'][-5000:]

    if not DRY:
        save(state, prules)
    for s, why in skipped:
        print('skip', s.get('ticker'), why)

    if buys or sells or notes:
        L = ['<b>🌱 ربات سیگنال رشد</b>' + (' (آزمایشی، بدون سفارش)' if DRY else '')]
        for pl, q, p in buys:
            L.append(f"🟢 خرید <b>{pl['ticker']}</b> ({pl['signal'].get('name', '')}) × {fa(q)} به قیمت حدود {fa(f'{p:,.2f}')}$ "
                     f"(حدود {fa(f'{q * p:,.0f}')}$)")
        for lot, p, ret, why in sells:
            L.append(f"🔴 فروش <b>{lot['ticker']}</b>: {fa(f'{ret * 100:+.1f}')}٪ — {why}")
        inv = sum(l['qty'] * l['entry'] for l in state['lots'])
        L.append(f"<i>سهم‌های باز: {fa(len(state['lots']))} از {fa(rules['max_positions'])} | سرمایه‌ی درگیر: "
                 f"{fa(f'{inv:,.0f}')} از {fa(f'{rules['capital_usd']:,}')}$</i>")
        L += [f'<i>⚠️ {n}</i>' for n in notes[:5]]
        if queued:
            L.append('<i>بازار بسته بود؛ سفارش‌ها در بازگشایی جلسه‌ی بعدی اجرا می‌شوند.</i>')
        L.append('<i>حساب آزمایشی Alpaca. خروج: حد ضرر، افت از سقف، یا پایان مدت نگهداری.</i>')
        tg('\n'.join(L))
    return 0


if __name__ == '__main__':
    sys.exit(main())
