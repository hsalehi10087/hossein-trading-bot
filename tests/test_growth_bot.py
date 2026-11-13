import copy

import growth_bot as G
from engine import conductor as PC

R = G.load_rules()
T = '2026-11-13'


def sig(t, price=100.0, streak=2):
    return {'ticker': t, 'name': t, 'price': price, 'profit_streak': streak}


def lot(t='AAA', entry=100.0, peak=None, date='2026-11-01', **k):
    return {'ticker': t, 'qty': 5, 'entry': entry, 'peak': peak or entry, 'date': date, 'filled': True, **k}


def test_rules_valid_and_paper_only():
    assert R['stage'] == 'paper' and R['trade_usd'] * R['max_positions'] <= R['capital_usd']


def test_entry_basic_and_sizing():
    plans, skipped = G.plan_entries(T, [sig('AAA', 100)], G.new_state(), R, {'AAA': 101.0}, T)
    assert len(plans) == 1 and plans[0]['qty'] == int(R['trade_usd'] // 101.0) and not skipped


def test_entry_filters():
    st = G.new_state()
    st['lots'].append(lot('HELD'))
    st['closed'].append({'ticker': 'SOLD', 'date_out': '2026-11-05'})
    st['processed'].append(f'{T}:DONE')
    sigs = [sig('HELD'), sig('SOLD'), sig('DONE'), sig('CHASE', 100), sig('CHEAP', 3), sig('NOPX'), sig('OTHER'),
            sig('BIG', 900), sig('OK')]
    px = {'HELD': 100, 'SOLD': 100, 'DONE': 100, 'CHASE': 106, 'CHEAP': 3, 'OTHER': 100, 'BIG': 900, 'OK': 100}
    plans, skipped = G.plan_entries(T, sigs, st, R, px, T, taken={'OTHER'})
    assert [p['ticker'] for p in plans] == ['OK']
    assert {s['ticker'] for s, _ in skipped} == {'HELD', 'SOLD', 'CHASE', 'CHEAP', 'NOPX', 'OTHER', 'BIG'}


def test_stale_signal_not_bought():
    plans, skipped = G.plan_entries('2026-11-08', [sig('AAA')], G.new_state(), R, {'AAA': 100}, T)
    assert not plans and skipped[0][1] == 'سیگنال کهنه'
    plans, _ = G.plan_entries('2026-11-14', [sig('AAA')], G.new_state(), R, {'AAA': 100}, T)   # تاریخ آینده
    assert not plans


def test_max_positions():
    st = G.new_state()
    st['lots'] = [lot(f'L{i}') for i in range(R['max_positions'] - 1)]
    plans, skipped = G.plan_entries(T, [sig('A'), sig('B')], st, R, {'A': 100, 'B': 100}, T)
    assert [p['ticker'] for p in plans] == ['A'] and skipped[0][1] == 'سقف تعداد سهم پر است'


def test_exits():
    assert G.exit_reason(lot(), 84.9, R, T).startswith('حد ضرر')
    assert G.exit_reason(lot(), 86.0, R, T) is None
    assert G.exit_reason(lot(peak=150), 119.0, R, T).startswith('افت')          # ۲۰٪ زیر سقف ۱۵۰
    assert G.exit_reason(lot(peak=150), 125.0, R, T) is None
    assert G.exit_reason(lot(date='2026-05-01'), 110.0, R, T).startswith('پایان')
    assert G.exit_reason(lot(), None, R, T) is None                              # قیمت نامعلوم = کاری نکن


def test_reconcile_fill_and_dead():
    st = G.new_state()
    st['lots'] = [{'ticker': 'A', 'qty': 5, 'entry': 100, 'peak': 100, 'date': T, 'order_id': '1', 'filled': False},
                  {'ticker': 'B', 'qty': 5, 'entry': 100, 'peak': 100, 'date': T, 'order_id': '2', 'filled': False},
                  {'ticker': 'C', 'qty': 5, 'entry': 100, 'peak': 100, 'date': T, 'order_id': '3', 'filled': False},
                  {'ticker': 'D', 'qty': 5, 'entry': 100, 'peak': 100, 'date': T, 'order_id': '4', 'filled': False}]
    orders = {'1': {'status': 'filled', 'filled_qty': '5', 'filled_avg_price': '101.5'},
              '2': {'status': 'rejected', 'filled_qty': '0'},
              '3': {'status': 'accepted', 'filled_qty': '0'},
              '4': None}
    notes = G.reconcile(st, orders.get)
    assert [l['ticker'] for l in st['lots']] == ['A', 'C', 'D']
    assert st['lots'][0]['entry'] == 101.5 and st['lots'][0]['filled'] and not st['lots'][1]['filled']
    assert len(notes) == 1 and 'B' in notes[0]


# ---- مالکیت پویا در هماهنگ‌کننده ----
def test_dynamic_owner_and_claim(tmp_path):
    r = copy.deepcopy(PC.load())
    r['_dynamic'] = {}
    assert PC.owner_of(r, 'CF') == 'copy_trade'                       # پیش‌فرض: کپی ترید
    assert PC.claim(r, 'CF', 'growth_signal') and PC.owner_of(r, 'CF') == 'growth_signal'
    assert not PC.claim(r, 'GLD', 'growth_signal') and PC.owner_of(r, 'GLD') == 'gld_trend'   # صاحب صریح عوض نمی‌شود
    assert not PC.claim(r, 'CF', 'copy_trade')                        # ربات بدون symbols_file نمی‌تواند ثبت کند
    assert not PC.claim(r, 'CF261120C00100000', 'growth_signal')      # اختیار معامله نه
    acct = {'equity': 100000, 'cash': 50000}
    c = PC.Conductor(r, acct, [], str(tmp_path / 's.json'))
    q, _ = c.approve('growth_signal', {'symbol': 'CF', 'side': 'buy', 'qty': 4, 'price': 120})
    assert q == 4
    q, ck = c.approve('copy_trade', {'symbol': 'CF', 'side': 'buy', 'qty': 1, 'price': 120})
    assert q == 0 and any(x['name'] == 'portfolio:symbol_owner' and not x['passed'] for x in ck)
    q, ck = c.approve('growth_signal', {'symbol': 'TSLA', 'side': 'buy', 'qty': 1, 'price': 120})   # ثبت‌نشده
    assert q == 0


def test_dynamic_file_loaded(tmp_path):
    (tmp_path / 'growth').mkdir()
    (tmp_path / 'growth' / 'owned.json').write_text('["CF", "GLD"]')
    r = PC.load()
    d = PC.load_dynamic(r, str(tmp_path))
    r['_dynamic'] = d
    assert PC.owner_of(r, 'CF') == 'growth_signal' and PC.owner_of(r, 'GLD') == 'gld_trend'
    (tmp_path / 'growth' / 'owned.json').write_text('not json')
    assert PC.load_dynamic(r, str(tmp_path)) == {}


def test_growth_sleeve_budget(tmp_path):
    r = copy.deepcopy(PC.load())
    r['_dynamic'] = {'AAA': 'growth_signal', 'BBB': 'growth_signal'}
    pos = [{'symbol': 'AAA', 'qty': '100', 'current_price': '105', 'market_value': '10500'}]
    c = PC.Conductor(r, {'equity': 100000, 'cash': 50000}, pos, str(tmp_path / 's.json'))
    q, _ = c.approve('growth_signal', {'symbol': 'BBB', 'side': 'buy', 'qty': 10, 'price': 100})
    assert q == 5      # سقف ۱۰٬۰۰۰ + ۱۰٪ = ۱۱٬۰۰۰؛ ۱۰٬۵۰۰ پر است ⇒ فقط ۵۰۰ دلار جا


def test_profit_streak_required():
    sigs = [sig('ONE', streak=1), sig('NONE', streak=None), sig('TWO', streak=2)]
    plans, skipped = G.plan_entries(T, sigs, G.new_state(), R, {'ONE': 100, 'NONE': 100, 'TWO': 100}, T)
    assert [p['ticker'] for p in plans] == ['TWO'] and {s['ticker'] for s, _ in skipped} == {'ONE', 'NONE'}
