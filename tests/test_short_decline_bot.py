import copy

import short_decline_bot as D
from engine import conductor as PC

R = D.load_rules()
T = '2026-11-20'


def sig(t, price=100.0, kind='losers', streak=1, sig_date=T):
    s = {'ticker': t, 'name': t, 'price': price, '_sig_date': sig_date, '_kind': kind}
    if kind == 'fund_decline':
        s['decline_streak'] = streak
    return s


def lot(t='AAA', entry=100.0, trough=None, date='2026-11-10', **k):
    return {'ticker': t, 'qty': 5, 'entry': entry, 'trough': trough or entry, 'date': date, 'filled': True, **k}


def test_rules_valid_and_paper_only():
    assert R['stage'] == 'paper'
    assert R['trade_usd'] * R['max_positions'] * R['short_margin_factor'] <= R['capital_usd'] * 1.001


def test_entry_basic_and_sizing():
    plans, skipped = D.plan_entries([sig('AAA', 100)], D.new_state(), R, {'AAA': 99.0}, T)
    assert len(plans) == 1 and plans[0]['qty'] == int(R['trade_usd'] // 99.0) and not skipped


def test_entry_filters():
    st = D.new_state()
    st['lots'].append(lot('HELD'))
    st['closed'].append({'ticker': 'COVERED', 'date_out': '2026-11-15'})
    st['processed'].append(f"losers:{T}:DONE")
    sigs = [sig('HELD'), sig('COVERED'), sig('DONE'), sig('CHASE', 100), sig('CHEAP', 3), sig('NOPX'), sig('OTHER'),
            sig('BIG', 900), sig('OK')]
    px = {'HELD': 100, 'COVERED': 100, 'DONE': 100, 'CHASE': 94, 'CHEAP': 3, 'OTHER': 100, 'BIG': 900, 'OK': 100}
    plans, skipped = D.plan_entries(sigs, st, R, px, T, taken={'OTHER'})
    assert [p['ticker'] for p in plans] == ['OK']
    assert {s['ticker'] for s, _ in skipped} == {'HELD', 'COVERED', 'CHASE', 'CHEAP', 'NOPX', 'OTHER', 'BIG'}


def test_chase_direction_is_inverted_for_shorts():
    # قیمت سیگنال ۱۰۰ بود، الان ۹۴ (افت ۶٪ بیشتر از سیگنال): جاماندگی، شورت نمی‌شود
    plans, skipped = D.plan_entries([sig('X', 100)], D.new_state(), R, {'X': 94}, T)
    assert not plans and skipped[0][1].startswith('جاماندگی')
    # قیمت کمی بالاتر از سیگنال (هنوز فرصت شورت هست): مجاز
    plans, _ = D.plan_entries([sig('Y', 100)], D.new_state(), R, {'Y': 97}, T)
    assert plans and plans[0]['ticker'] == 'Y'


def test_stale_signal_not_shorted():
    plans, skipped = D.plan_entries([sig('AAA', sig_date='2026-11-10')], D.new_state(), R, {'AAA': 100}, T)
    assert not plans and skipped[0][1] == 'سیگنال کهنه'


def test_max_positions():
    st = D.new_state()
    st['lots'] = [lot(f'L{i}') for i in range(R['max_positions'] - 1)]
    plans, skipped = D.plan_entries([sig('A'), sig('B')], st, R, {'A': 100, 'B': 100}, T)
    assert [p['ticker'] for p in plans] == ['A'] and skipped[0][1] == 'سقف تعداد سهم پر است'


def test_decline_streak_required_only_for_fund_decline():
    sigs = [sig('ONE', kind='fund_decline', streak=0), sig('TWO', kind='fund_decline', streak=1),
            sig('THREE', kind='losers')]   # سیگنال قیمتی هیچ شرط streak ندارد
    plans, skipped = D.plan_entries(sigs, D.new_state(), R, {'ONE': 100, 'TWO': 100, 'THREE': 100}, T)
    assert {p['ticker'] for p in plans} == {'TWO', 'THREE'}
    assert {s['ticker'] for s, _ in skipped} == {'ONE'}


def test_exits_inverted_for_shorts():
    # حد ضرر: قیمت بالا رفت (برخلاف خرید که افت‌اش ضرر است)
    assert D.exit_reason(lot(), 110.1, R, T).startswith('حد ضرر')
    assert D.exit_reason(lot(), 109.0, R, T) is None
    # بازگشت از کف: قیمت تا ۸۰ افت کرد (trough=80)، بعد ۲۰٪ بالا آمد (96) → پوشش با سود
    assert D.exit_reason(lot(trough=80), 96.0, R, T).startswith('بازگشت')
    assert D.exit_reason(lot(trough=80), 95.0, R, T) is None
    assert D.exit_reason(lot(date='2026-05-01'), 100.0, R, T).startswith('پایان')
    assert D.exit_reason(lot(), None, R, T) is None


def test_reconcile_fill_and_dead():
    st = D.new_state()
    st['lots'] = [{'ticker': 'A', 'qty': 5, 'entry': 100, 'trough': 100, 'date': T, 'order_id': '1', 'filled': False},
                  {'ticker': 'B', 'qty': 5, 'entry': 100, 'trough': 100, 'date': T, 'order_id': '2', 'filled': False},
                  {'ticker': 'C', 'qty': 5, 'entry': 100, 'trough': 100, 'date': T, 'order_id': '3', 'filled': False},
                  {'ticker': 'D', 'qty': 5, 'entry': 100, 'trough': 100, 'date': T, 'order_id': '4', 'filled': False}]
    orders = {'1': {'status': 'filled', 'filled_qty': '5', 'filled_avg_price': '98.5'},
              '2': {'status': 'rejected', 'filled_qty': '0'},
              '3': {'status': 'accepted', 'filled_qty': '0'},
              '4': None}
    notes = D.reconcile(st, orders.get)
    assert [l['ticker'] for l in st['lots']] == ['A', 'C', 'D']
    assert st['lots'][0]['entry'] == 98.5 and st['lots'][0]['filled'] and not st['lots'][1]['filled']
    assert len(notes) == 1 and 'B' in notes[0]


# ---- هماهنگ‌کننده: فقط همین sleeve اجازه‌ی شورت دارد ----
def test_short_sleeve_owns_itself_and_can_short(tmp_path):
    r = copy.deepcopy(PC.load())
    r['_dynamic'] = {}
    assert PC.claim(r, 'XYZ', 'decline_short') and PC.owner_of(r, 'XYZ') == 'decline_short'
    c = PC.Conductor(r, {'equity': 100000, 'cash': 90000}, [], str(tmp_path / 's.json'))
    q, ck = c.approve('decline_short', {'symbol': 'XYZ', 'side': 'sell', 'qty': 20, 'price': 100})
    assert q == 20 and c.pos['XYZ']['qty'] == -20
    assert any(x['name'] == 'portfolio:short_allowed' and x['passed'] for x in ck)
    q2, _ = c.approve('decline_short', {'symbol': 'XYZ', 'side': 'buy', 'qty': 20, 'price': 90})
    assert q2 == 20 and c.pos['XYZ']['qty'] == 0


def test_other_sleeves_cannot_short(tmp_path):
    r = PC.load()
    q, ck = PC.Conductor(r, {'equity': 100000, 'cash': 90000}, [], str(tmp_path / 's.json')).approve(
        'copy_trade', {'symbol': 'ZZZ', 'side': 'sell', 'qty': 10, 'price': 50})
    assert q == 0


def test_broker_stop_price_uses_rules_stop_loss():
    assert D.stop_price({'entry': 100.0}, R) == round(100 * (1 + R['stop_loss_pct']), 2)
    assert D.stop_price({'entry': 6.0}, R) == 6.6
