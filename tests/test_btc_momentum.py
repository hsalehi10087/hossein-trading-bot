import btc_momentum as B
from engine import conductor as PC


def test_rules():
    r = B.load_rules()
    assert r['lookback_days'] == 28 and r['capital_usd'] == 5000


def test_signal_up_down():
    up = [100 + i for i in range(40)]
    down = [200 - i for i in range(40)]
    assert B.signal(up, 28)[0] == 1 and B.signal(down, 28)[0] == 0


def test_target_qty():
    assert B.target_qty(1, 5000, 100000) == 0.05 and B.target_qty(0, 5000, 100000) == 0


def test_btc_owned_by_its_sleeve():
    R = PC.load()
    assert PC.owner_of(R, 'BTCUSD') == 'btc_momentum' and PC.owner_of(R, 'BTC/USD') == 'btc_momentum'
