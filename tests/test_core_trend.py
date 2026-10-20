import datetime as dt
import os

import numpy as np
import pandas as pd

from engine import core_trend as C

R = C.load(os.path.join(os.path.dirname(__file__), '..', 'rules', 'qqq_trend.yaml'))
EP = 'https://paper-api.alpaca.markets/v2'


def series(vals):
    return pd.Series(vals, index=pd.bdate_range('2020-01-01', periods=len(vals)), dtype=float)


def test_rules_valid():
    assert C.validate(R) == [] and R['stage'] == 'paper'


def test_invalid_rules():
    bad = dict(R, params={'sma_n': 20, 'band': 0.2})
    assert C.validate(bad)


def test_no_lookahead():
    rng = np.random.default_rng(3)
    s = series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 900))))
    full, _ = C.trend_states(s, 200, 0.02)
    for i in range(250, 900, 41):
        part, _ = C.trend_states(s.iloc[:i + 1], 200, 0.02)
        assert part[-1] == full[i]


def test_band_hysteresis():
    s = series([100.0] * 200 + [101.0] * 5)          # ۱٪ بالای میانگین: داخل باند، ورود نه
    assert C.trend_states(s, 200, 0.02)[0][-1][0] == 0
    s = series([100.0] * 200 + [103.0] * 5)
    assert C.trend_states(s, 200, 0.02)[0][-1][0] == 1


def test_switch_orders_sell_first():
    s = series([100.0] * 200 + [110.0] * 5)
    d = C.decide(R, s, {'QQQ': 110.0, 'IEF': 95.0}, {'QQQ': 0, 'IEF': 200})
    assert [o['side'] for o in d['orders']] == ['sell', 'buy']
    assert d['orders'][0] == {'symbol': 'IEF', 'side': 'sell', 'qty': 200, 'price': 95.0}
    assert d['orders'][1]['symbol'] == 'QQQ' and d['orders'][1]['qty'] == int(20000 // 110)


def test_no_churn_small_drift():
    s = series([100.0] * 200 + [110.0] * 5)
    d = C.decide(R, s, {'QQQ': 110.0, 'IEF': 95.0}, {'QQQ': 170, 'IEF': 0})
    assert d['orders'] == []


def test_risk_checks():
    s = series([100.0] * 200 + [110.0] * 5)
    d = C.decide(R, s, {'QQQ': 110.0, 'IEF': 95.0}, {'QQQ': 0, 'IEF': 0})
    o = d['orders'][0]
    today = dt.date.fromisoformat(d['data_date'])
    ok = lambda **kw: {c['name'] for c in C.checks(R, d, o, today, kw.get('n', 0), kw.get('dup', False),
                                                    kw.get('kill', False), kw.get('ep', EP)) if not c['passed']}
    assert ok() == set()
    assert 'kill_switch' in ok(kill=True)
    assert 'paper_only' in ok(ep='https://api.alpaca.markets/v2')
    assert 'not_duplicate' in ok(dup=True)
    assert 'max_orders_per_day' in ok(n=2)
    big = dict(o, qty=1000)
    assert 'max_order_usd' in {c['name'] for c in C.checks(R, d, big, today, 0, False, False, EP) if not c['passed']}
