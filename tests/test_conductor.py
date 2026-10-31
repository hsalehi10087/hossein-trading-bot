import copy

from engine import conductor as PC

R = PC.load()


def mk(equity=86000, cash=57000, positions=None, peak=None, tmp=None):
    pos = positions if positions is not None else [
        {'symbol': 'QQQ', 'qty': '27', 'current_price': '720', 'market_value': '19440'},
        {'symbol': 'KRMN', 'qty': '15', 'current_price': '35', 'market_value': '525'}]
    path = str(tmp / 'state.json')
    if peak:
        open(path, 'w').write('{"peak_equity": %s}' % peak)
    return PC.Conductor(R, {'equity': equity, 'cash': cash}, pos, path)


def test_rules_valid_and_unique_owner():
    assert PC.validate(R) == []
    bad = copy.deepcopy(R)
    bad['sleeves']['qqq_trend']['symbols'].append('GLD')
    assert any('owned by both' in e for e in PC.validate(bad))


def test_owner_of_options():
    assert PC.owner_of(R, 'KRMN261120C00035000') == 'legacy_1h'
    assert PC.owner_of(R, 'QQQ') == 'qqq_trend'
    assert PC.owner_of(R, 'TSLA') is None


def test_orphans(tmp_path):
    c = mk(positions=[{'symbol': 'VT', 'qty': '10', 'current_price': '100', 'market_value': '1000'}], tmp=tmp_path)
    assert c.orphans() == [('VT', 10.0, 1000.0)]


def test_wrong_owner_blocked(tmp_path):
    c = mk(tmp=tmp_path)
    q, ck = c.approve('gld_trend', {'symbol': 'QQQ', 'side': 'buy', 'qty': 1, 'price': 720})
    assert q == 0 and any(x['name'] == 'portfolio:symbol_owner' and not x['passed'] for x in ck)


def test_sleeve_budget_scales_buy(tmp_path):
    c = mk(tmp=tmp_path)   # QQQ sleeve already ~19.4k of 20k (+10% tolerance = 22k)
    q, _ = c.approve('qqq_trend', {'symbol': 'IEF', 'side': 'buy', 'qty': 100, 'price': 95})
    assert 0 < q < 100 and q == (22000 - 19440) // 95


def test_no_margin(tmp_path):
    c = mk(cash=1500, tmp=tmp_path)
    q, _ = c.approve('gld_trend', {'symbol': 'GLD', 'side': 'buy', 'qty': 20, 'price': 380})
    assert q == 1  # (1500 - 1000 reserve) // 380


def test_legacy_sleeve_capped_at_5k(tmp_path):
    c = mk(positions=[{'symbol': 'KRMN261120C00035000', 'qty': '8', 'current_price': '590', 'market_value': '4720'}],
           tmp=tmp_path)
    # سقف ۵۰۰۰ + ۱۰٪ = ۵۵۰۰؛ اختیار KRMN با ۴۷۲۰ دلار فقط ۷۸۰ دلار جا می‌گذارد
    assert c.approve('legacy_1h', {'symbol': 'NVDA', 'side': 'buy', 'qty': 10, 'price': 200})[0] == 3
    assert c.approve('legacy_1h', {'symbol': 'TSLA', 'side': 'buy', 'qty': 1, 'price': 100})[0] == 0   # صاحب ندارد
    assert c.approve('legacy_1h', {'symbol': 'KRMN261120C00035000', 'side': 'sell', 'qty': 8, 'price': 590})[0] == 8


def test_legacy_buys_off_when_disabled(tmp_path):
    r = copy.deepcopy(R)
    r['sleeves']['legacy_1h']['buys_allowed'] = False
    c = PC.Conductor(r, {'equity': 86000, 'cash': 57000}, [], str(tmp_path / 's.json'))
    assert c.approve('legacy_1h', {'symbol': 'NVDA', 'side': 'buy', 'qty': 1, 'price': 200})[0] == 0


def test_no_short(tmp_path):
    c = mk(tmp=tmp_path)
    assert c.approve('qqq_trend', {'symbol': 'QQQ', 'side': 'sell', 'qty': 30, 'price': 720})[0] == 0


def test_brake_halves_budget(tmp_path):
    c = mk(equity=70000, peak=86000, positions=[], tmp=tmp_path)   # −18.6%
    assert c.brake
    q, _ = c.approve('gld_trend', {'symbol': 'GLD', 'side': 'buy', 'qty': 30, 'price': 380})
    assert q == int(10000 * 0.5 * 1.1 // 380)


def test_sequential_orders_share_cash(tmp_path):
    c = mk(cash=10000, positions=[], tmp=tmp_path)
    q1, _ = c.approve('gld_trend', {'symbol': 'GLD', 'side': 'buy', 'qty': 20, 'price': 380})
    q2, _ = c.approve('qqq_trend', {'symbol': 'QQQ', 'side': 'buy', 'qty': 27, 'price': 720})
    assert q1 == 20 and q2 == (10000 - 1000 - 20 * 380) // 720
