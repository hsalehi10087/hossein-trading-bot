"""ربات کپی ترید: معاملات تریدرهای بزرگ (13F، ARK، کنگره، خرید گروهی مدیران) را کپی می‌کند.

داده‌ی رویدادها و رهبرها از stock-scanner/data/copytrade می‌آید (copytrade/collect.py).
قواعد: rules/copy_trade.yaml. هر خرید از هماهنگ‌کننده‌ی سبد (sleeve: copy_trade) اجازه می‌گیرد.
وضعیت: datastore/copytrade/state.json  {processed, lots, closed, leaders}

هر اجرا (روزهای کاری، وقتی بازار باز است):
  ۱) خروج‌ها: حد ضرر ۱۵٪ هر معامله؛ فروش وقتی خود تریدر بفروشد
  ۲) شمارش ضرر: معامله‌ی بسته با زیان، یا معامله‌ی باز که ۱۰٪ زیر ورود برود (یک بار) = یک ضرر
     سود = ضررهای پشت سر هم صفر می‌شود. ۳ ضرر پشت سر هم ← حذف تریدر
  ۳) عملکرد ۶ ماهه: اگر بازده کپی تریدر ۱۰٪ از S&P 500 عقب باشد (حداقل ۳ معامله) ← حذف
  ۴) ورودها: رویدادهای خرید تازه از رهبرهای فعال؛ فیلتر جاماندگی ۱۵٪؛ ۵۰۰ دلار (۲ برابر اگر ≥۲ تریدر)،
     سقف هر معامله ۱۰۰۰، سقف هر تریدر ۲۰۰۰، سقف کل ۱۰٬۰۰۰
  ۵) گزارش تلگرام یکجا
"""
import datetime as dt
import hashlib
import json
import os
import sys

import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import conductor as PC  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.getenv('DATASTORE', 'datastore')
STATE = os.path.join(STORE, 'copytrade', 'state.json')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'):
    ENDPOINT += '/v2'
DATA = 'https://data.alpaca.markets/v2'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
SLEEVE = 'copy_trade'
SRC_FA = {'13F': '🏦', 'ARK': '🚀', 'Congress': '🏛', 'Insiders': '👔'}
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'


def fa(x):
    return str(x).translate(str.maketrans('0123456789.-', '۰۱۲۳۴۵۶۷۸۹٫−'))


def load_rules(path=os.path.join(ROOT, 'rules', 'copy_trade.yaml')):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    need = ['version', 'capital_usd', 'per_leader_usd', 'base_trade_usd', 'max_trade_usd', 'max_chase_pct',
            'stop_loss_pct', 'loss_mark_pct', 'max_consecutive_losses', 'perf_window_days', 'perf_lag_vs_spy']
    miss = [k for k in need if k not in r]
    if miss:
        raise ValueError(f'copy_trade rules missing {miss}')
    if not (0 < r['stop_loss_pct'] < 0.5 and r['max_trade_usd'] <= r['per_leader_usd'] <= r['capital_usd']):
        raise ValueError('copy_trade rules out of range')
    return r


def new_state():
    return {'processed': [], 'lots': [], 'closed': [], 'leaders': {}}


# ---------------------------------------------------------------- منطق خالص (قابل آزمون)
def leader_rec(state, name):
    return state['leaders'].setdefault(name, {'consec': 0, 'losses': 0, 'wins': 0, 'removed': False,
                                              'reason': '', 'removed_date': None})


def register_result(state, rules, leader, is_loss, today):
    """یک نتیجه (ضرر یا سود) برای تریدر ثبت می‌کند؛ اگر به ۳ ضرر پشت سر هم رسید، حذفش می‌کند."""
    L = leader_rec(state, leader)
    if is_loss:
        L['consec'] += 1
        L['losses'] += 1
        if L['consec'] >= rules['max_consecutive_losses'] and not L['removed']:
            L.update(removed=True, reason=f"{L['consec']} ضرر پشت سر هم", removed_date=today)
            return True
    else:
        L['consec'] = 0
        L['wins'] += 1
    return False


def perf_check(state, rules, leader, prices, spy_now, today):
    """بازده کپی تریدر در پنجره‌ی ۶ ماهه در برابر SPY. خروجی: (بازده، بازده SPY) یا None."""
    cut = str(dt.date.fromisoformat(today) - dt.timedelta(days=rules['perf_window_days']))
    rets, spys = [], []
    for c in state['closed']:
        if c['leader'] == leader and c['date_out'] >= cut:
            rets.append(c['ret'])
            spys.append(c.get('spy_ret') or 0)
    for lot in state['lots']:
        if lot['leader'] == leader and lot['date'] >= cut and prices.get(lot['ticker']):
            rets.append(prices[lot['ticker']] / lot['entry'] - 1)
            spys.append(spy_now / lot['spy_entry'] - 1 if lot.get('spy_entry') else 0)
    if len(rets) < rules.get('perf_min_trades', 3):
        return None
    return sum(rets) / len(rets), sum(spys) / len(spys)


def plan_entries(events, leaders_ok, state, rules, prices, today):
    """رویدادهای خرید ← فهرست سفارش {ticker, leader(s), usd}. فیلترها: تکراری، رهبر غیرفعال، کهنه، جاماندگی."""
    cut = str(dt.date.fromisoformat(today) - dt.timedelta(days=rules.get('event_max_age_days', 10)))
    win = str(dt.date.fromisoformat(today) - dt.timedelta(days=rules.get('cluster_window_days', 10)))
    done = set(state['processed'])
    buyers = {}
    for e in events:
        if e['side'] == 'buy' and e['seen'] >= win and leaders_ok(e['leader']):
            buyers.setdefault(e['ticker'], set()).add(e['leader'])
    open_by_leader, held = {}, {}
    for lot in state['lots']:
        open_by_leader[lot['leader']] = open_by_leader.get(lot['leader'], 0) + lot['qty'] * lot['entry']
        held.setdefault(lot['ticker'], set()).add(lot['leader'])
    plans, skipped = [], []
    for e in events:
        if e['id'] in done or e['side'] != 'buy':
            continue
        t, L = e['ticker'], e['leader']
        why = None
        if e['seen'] < cut:
            why = 'کهنه'
        elif not leaders_ok(L):
            why = 'تریدر فعال نیست'
        elif L in held.get(t, set()):
            why = 'قبلاً کپی شده'
        elif not prices.get(t):
            why = 'قیمت نامعلوم'
        elif prices[t] < rules.get('min_price_usd', 5):
            why = 'قیمت زیر ۵ دلار'
        elif e.get('ref_price') and prices[t] > e['ref_price'] * (1 + rules['max_chase_pct']):
            why = f"جاماندگی {prices[t] / e['ref_price'] - 1:+.0%}"
        if why:
            skipped.append((e, why))
            continue
        usd = rules['base_trade_usd'] * (rules.get('cluster_multiplier', 2) if len(buyers.get(t, ())) >= 2 else 1)
        usd = min(usd, rules['max_trade_usd'], rules['per_leader_usd'] - open_by_leader.get(L, 0))
        if usd < prices[t]:
            skipped.append((e, 'سقف سرمایه‌ی تریدر پر است'))
            continue
        plans.append({'event': e, 'ticker': t, 'leader': L, 'usd': usd, 'cluster': len(buyers.get(t, ())) >= 2})
        open_by_leader[L] = open_by_leader.get(L, 0) + usd
        held.setdefault(t, set()).add(L)
    return plans, skipped


# ---------------------------------------------------------------- Alpaca
def get(path, base=ENDPOINT, **params):
    r = requests.get(base + path, headers=H, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


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
    chats = [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']
    print(text)
    for c in chats:
        for i in range(0, len(text), 3900):
            requests.post(f'https://api.telegram.org/bot{tok}/sendMessage', timeout=30,
                          data={'chat_id': c, 'text': text[i:i + 3900], 'parse_mode': 'HTML',
                                'disable_web_page_preview': 'true'})


# ---------------------------------------------------------------- اجرا
def main():
    rules = load_rules()
    prules = PC.load()
    today = str(dt.date.today())
    clock = get('/clock')
    market_open = bool(clock.get('is_open')) or DRY or '--force' in sys.argv   # بسته: فقط گزارش، بدون معامله
    base = rules['source_url'].rstrip('/')
    events = [json.loads(l) for l in requests.get(base + '/events.jsonl', timeout=30).text.splitlines() if l.strip()]
    lj = requests.get(base + '/leaders.json', timeout=30).json()
    sel = lj.get('leaders', {})
    state = json.load(open(STATE)) if os.path.exists(STATE) else new_state()
    for name in sel:
        leader_rec(state, name)

    def ok(name):
        return bool(sel.get(name, {}).get('active')) and not leader_rec(state, name)['removed']

    acct, positions = get('/account'), get('/positions')
    pos = {p['symbol']: p for p in positions}
    cond = PC.Conductor(prules, acct, positions, os.path.join(STORE, 'portfolio', 'state.json'))
    tick = {l['ticker'] for l in state['lots']} | {e['ticker'] for e in events if e['side'] == 'buy'} | {'SPY'}
    prices = latest_prices(tick)
    spy = prices.get('SPY')
    done_buys, done_sells, removed_now, notes = [], [], [], []

    plans, skipped = [], []
    if not market_open:
        notes.append('بازار امروز بسته است؛ فقط گزارش، بدون معامله')
        report(state, sel, prices, done_buys, done_sells, removed_now, skipped, notes, today, events)
        return 0
    # ۱) خروج‌ها: حد ضرر یا فروش تریدر
    sells = {(e['leader'], e['ticker']) for e in events if e['side'] == 'sell' and e['id'] not in state['processed']}
    keep = []
    for lot in state['lots']:
        p = prices.get(lot['ticker'])
        reason = None
        if p and p <= lot['entry'] * (1 - rules['stop_loss_pct']):
            reason = 'حد ضرر ۱۵٪'
        elif (lot['leader'], lot['ticker']) in sells:
            reason = 'تریدر فروخت'
        if reason and lot['ticker'] in pos:
            have = int(float(pos[lot['ticker']]['qty']))
            q = min(lot['qty'], have)
            if q >= 1 and cond.approve(SLEEVE, {'symbol': lot['ticker'], 'side': 'sell', 'qty': q, 'price': p})[0] \
                    and order(lot['ticker'], q, 'sell', f"ct-s-{hashlib.md5(lot['id'].encode()).hexdigest()[:16]}-{today}"):
                ret = p / lot['entry'] - 1
                spy_ret = spy / lot['spy_entry'] - 1 if spy and lot.get('spy_entry') else None
                state['closed'].append({'leader': lot['leader'], 'ticker': lot['ticker'], 'entry': lot['entry'], 'exit': p,
                                        'ret': round(ret, 4), 'spy_ret': spy_ret, 'date_in': lot['date'],
                                        'date_out': today, 'reason': reason})
                done_sells.append((lot, p, ret, reason))
                if not lot.get('loss_marked'):         # اگر قبلاً ضرر حساب نشده بود
                    if register_result(state, rules, lot['leader'], ret < 0, today):
                        removed_now.append(lot['leader'])
                continue
        elif reason is None and lot['ticker'] not in pos:
            notes.append(f"{lot['ticker']}: در حساب نیست (دستی بسته شده؟) — از فهرست حذف شد")
            continue
        # معامله‌ی باز که ۱۰٪ زیر ورود رفته: یک ضرر (فقط یک بار)
        if p and not lot.get('loss_marked') and p <= lot['entry'] * (1 - rules['loss_mark_pct']):
            lot['loss_marked'] = True
            if register_result(state, rules, lot['leader'], True, today):
                removed_now.append(lot['leader'])
        keep.append(lot)
    state['lots'] = keep

    # ۲) عملکرد ۶ ماهه
    for name, L in state['leaders'].items():
        if L['removed'] or not spy:
            continue
        pc = perf_check(state, rules, name, prices, spy, today)
        if pc and pc[0] < pc[1] - rules['perf_lag_vs_spy']:
            L.update(removed=True, reason=f"۶ ماه: {pc[0]:+.0%} در برابر S&P {pc[1]:+.0%}", removed_date=today)
            removed_now.append(name)

    # ۳) ورودها
    plans, skipped = plan_entries(events, ok, state, rules, prices, today)
    for pl in plans:
        t, p = pl['ticker'], prices[pl['ticker']]
        qty = int(pl['usd'] // p)
        q, checks = cond.approve(SLEEVE, {'symbol': t, 'side': 'buy', 'qty': qty, 'price': p})
        if q < 1:
            skipped.append((pl['event'], 'هماهنگ‌کننده: ' + ', '.join(c['name'].split(':')[-1] for c in checks if not c['passed'])))
            continue
        if order(t, q, 'buy', f"ct-b-{hashlib.md5(pl['event']['id'].encode()).hexdigest()[:16]}-{today}"):
            state['lots'].append({'id': pl['event']['id'], 'leader': pl['leader'], 'ticker': t, 'qty': q, 'entry': p,
                                  'date': today, 'spy_entry': spy, 'source': pl['event']['source']})
            done_buys.append((pl, q, p))
    # همه‌ی رویدادهای دیده‌شده پردازش‌شده‌اند (ردشده‌ها هم دوباره بررسی نمی‌شوند)
    state['processed'] = sorted(set(state['processed']) | {e['id'] for e in events})[-20000:]

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, 'w'), ensure_ascii=False, indent=1)
    report(state, sel, prices, done_buys, done_sells, removed_now, skipped, notes, today, events)
    return 0


def report(state, sel, prices, buys, sells, removed, skipped, notes, today, events=()):
    L = [f'<b>👥 کپی ترید — {fa(today.replace("-", "/"))}</b>']
    fresh = [e for e in events if e['seen'] >= str(dt.date.fromisoformat(today) - dt.timedelta(days=1))
             and sel.get(e['leader'], {}).get('active')]
    if fresh:
        L.append('<b>👀 معاملات تازه‌ی تریدرها</b>')
        for e in fresh[:15]:
            L.append(f"{SRC_FA.get(e['source'], '')} {e['leader'].split(':', 1)[1][:30]}: "
                     f"{'خرید' if e['side'] == 'buy' else 'فروش'} <b>{e['ticker']}</b>")
    if buys:
        L.append('<b>🟢 خریدهای امروز</b>')
        for pl, q, p in buys:
            e = pl['event']
            L.append(f"{SRC_FA.get(e['source'], '')} <b>{pl['ticker']}</b> × {fa(q)} @ {fa(round(p, 2))}$ — "
                     f"{pl['leader'].split(':', 1)[1]}{' (۲ تریدر یا بیشتر ⇒ ۲ برابر)' if pl['cluster'] else ''}")
    if sells:
        L.append('<b>🔴 فروش‌های امروز</b>')
        for lot, p, ret, why in sells:
            L.append(f"<b>{lot['ticker']}</b> {fa(f'{ret * 100:+.1f}')}٪ — {why} ({lot['leader'].split(':', 1)[1]})")
    if removed:
        L.append('<b>⛔ حذف از فهرست</b>')
        for n in dict.fromkeys(removed):
            L.append(f"{n.split(':', 1)[1]} — {state['leaders'][n]['reason']}")
    if not (buys or sells or removed):
        L.append('امروز معامله‌ی تازه‌ای نبود.')
    # جدول تریدرها: فقط کسانی که کپی شده‌اند یا حذف شده‌اند
    rows = []
    for n, rec in state['leaders'].items():
        lots = [l for l in state['lots'] if l['leader'] == n]
        closed = [c for c in state['closed'] if c['leader'] == n]
        if not lots and not closed and not rec['removed']:
            continue
        upl = sum((prices.get(l['ticker'], l['entry']) - l['entry']) * l['qty'] for l in lots)
        rows.append((n, rec, len(lots), len(closed), upl))
    if rows:
        L.append('<b>📋 تریدرها</b> (باز | بسته | سود/زیان باز | ضرر پشت سر هم)')
        for n, rec, nl, nc, upl in sorted(rows, key=lambda x: (x[1]['removed'], -x[4])):
            st = '⛔' if rec['removed'] else '✅'
            L.append(f"{st} {SRC_FA.get(n.split(':')[0], '')} {n.split(':', 1)[1][:28]}: {fa(nl)} | {fa(nc)} | "
                     f"{fa(f'{upl:+,.0f}')}$ | {fa(rec['consec'])}/۳")
    if state['lots']:
        L.append('<b>💼 موقعیت‌های باز</b>')
        for l in sorted(state['lots'], key=lambda x: x['date']):
            p = prices.get(l['ticker'])
            r = f"{fa(f'{(p / l['entry'] - 1) * 100:+.1f}')}٪" if p else '—'
            L.append(f"<b>{l['ticker']}</b> × {fa(l['qty'])} از {fa(l['date'][5:].replace('-', '/'))}: {r} ({l['leader'].split(':', 1)[1][:24]})")
    active = sum(1 for n, v in sel.items() if v.get('active') and not state['leaders'].get(n, {}).get('removed'))
    inv = sum(l['qty'] * l['entry'] for l in state['lots'])
    L.append(f"<i>تریدرهای فعال: {fa(active)} | سرمایه‌ی درگیر: {fa(f'{inv:,.0f}')} از ۱۰٬۰۰۰ دلار | "
             f"رویدادهای ردشده امروز: {fa(len(skipped))}</i>")
    L += [f'<i>⚠️ {n}</i>' for n in notes[:5]]
    L.append('<i>🏦 13F  🚀 ARK  🏛 کنگره  👔 مدیران. حذف: ۳ ضرر پشت سر هم یا ۱۰٪ عقب‌تر از S&P در ۶ ماه. حساب آزمایشی.</i>')
    tg('\n'.join(L))


if __name__ == '__main__':
    sys.exit(main())
