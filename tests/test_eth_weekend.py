import datetime as dt

import eth_weekend as E
from engine import conductor as PC

R = E.load_rules()
SAT = dt.datetime(2026, 11, 14, 0, 10, tzinfo=dt.timezone.utc)
FRI, SATBAR, SUNBAR = dt.date(2026, 11, 13), dt.date(2026, 11, 14), dt.date(2026, 11, 15)


def test_rules_and_owner():
    assert R['capital_usd'] == 5000
    assert PC.owner_of(PC.load(), 'ETHUSD') == 'eth_weekend'


def test_entry_only_saturday_after_friday_bar():
    assert E.decide(SAT, FRI, 100, 100, 50, None, 100, R)[0] == 'buy'
    assert E.decide(SAT, FRI, 98.6, 100, 50, None, 100, R)[0] == 'buy'        # ≥ 0.985 × EMA
    assert E.decide(SAT, FRI, 98, 100, 80, None, 100, R)[0] is None           # RSI بالا
    assert E.decide(SAT, FRI, 90, 100, 50, None, 100, R)[0] is None           # زیر EMA
    late = SAT.replace(hour=13)
    assert E.decide(late, FRI, 100, 100, 50, None, 100, R)[0] is None         # خیلی دیر


def test_exits():
    pos = {'entry': 100}
    assert E.decide(SAT, FRI, 0, 0, 50, pos, 95, R)[1] == 'حد ضرر ۴٫۵٪'
    assert E.decide(SAT, FRI, 0, 0, 50, pos, 108, R)[1] == 'حد سود ۷٫۵٪'
    sun = SAT + dt.timedelta(days=1)
    assert E.decide(sun, SATBAR, 0, 0, 80, pos, 101, R)[0] == 'sell'
    assert E.decide(sun, SATBAR, 0, 0, 60, pos, 101, R)[0] is None
    mon = SAT + dt.timedelta(days=2)
    assert E.decide(mon, SUNBAR, 0, 0, 60, pos, 101, R)[1] == 'پایان آخر هفته'


def test_indicators_shape():
    c, e, r = E.indicators([100 + (i % 5) for i in range(120)], R)
    assert c > 0 and e > 0 and 0 <= r <= 100
