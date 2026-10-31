import copy_trade as CT

R = CT.load_rules()
T = '2026-11-02'


def ev(i, leader, t, side='buy', seen=T, ref=None):
    return {'id': f'{i}', 'leader': leader, 'ticker': t, 'side': side, 'seen': seen, 'ref_price': ref, 'source': '13F'}


def test_rules_valid():
    assert R['capital_usd'] == 10000 and R['max_consecutive_losses'] == 3


def test_three_consecutive_losses_remove():
    s = CT.new_state()
    assert not CT.register_result(s, R, 'A', True, T)
    assert not CT.register_result(s, R, 'A', True, T)
    assert CT.register_result(s, R, 'A', True, T)
    assert s['leaders']['A']['removed']


def test_win_resets_streak():
    s = CT.new_state()
    CT.register_result(s, R, 'A', True, T)
    CT.register_result(s, R, 'A', True, T)
    CT.register_result(s, R, 'A', False, T)
    assert not CT.register_result(s, R, 'A', True, T) and s['leaders']['A']['consec'] == 1


def test_entries_sizing_cluster_chase_and_caps():
    s = CT.new_state()
    evs = [ev(1, 'A', 'XYZ'), ev(2, 'B', 'XYZ'), ev(3, 'A', 'ABC', ref=100), ev(4, 'C', 'OLD', seen='2026-10-01'),
           ev(5, 'X', 'QQQ1')]
    prices = {'XYZ': 50, 'ABC': 120, 'OLD': 10, 'QQQ1': 10}
    plans, skipped = CT.plan_entries(evs, lambda n: n != 'X', s, R, prices, T)
    by = {(p['leader'], p['ticker']): p for p in plans}
    assert by[('A', 'XYZ')]['usd'] == 1000 and by[('A', 'XYZ')]['cluster']      # ۲ تریدر ⇒ ۲ برابر
    assert ('A', 'ABC') not in by                                                # جاماندگی ۲۰٪
    reasons = {e['ticker']: w for e, w in skipped}
    assert reasons['OLD'] == 'کهنه' and reasons['QQQ1'] == 'تریدر فعال نیست'


def test_per_leader_cap():
    s = CT.new_state()
    s['lots'] = [{'leader': 'A', 'ticker': 'Q1', 'qty': 19, 'entry': 100, 'date': T}]   # ۱۹۰۰ از ۲۰۰۰
    plans, skipped = CT.plan_entries([ev(1, 'A', 'NEW')], lambda n: True, s, R, {'NEW': 150}, T)
    assert not plans and skipped[0][1] == 'سقف سرمایه‌ی تریدر پر است'


def test_perf_removal_inputs():
    s = CT.new_state()
    s['closed'] = [{'leader': 'A', 'ret': -0.10, 'spy_ret': 0.05, 'date_out': T}] * 3
    r, b = CT.perf_check(s, R, 'A', {}, 500, T)
    assert r < b - R['perf_lag_vs_spy']
