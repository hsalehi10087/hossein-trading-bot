import datetime as dt

from engine import exit_rules as X

P = {'trail_stock': 0.12, 'hard_stop_stock': 0.20, 'option_stop': 0.40,
     'option_take_profit': 1.00, 'option_exit_days': 10}
TODAY = dt.date(2026, 10, 25)
OPT = 'KRMN261120C00035000'


def test_option_expiry_parsed():
    assert X.expiry(OPT) == dt.date(2026, 11, 20)
    assert X.is_option(OPT) and not X.is_option('CRGY')


def test_stock_hold_when_near_peak():
    assert X.decide('CRGY', 99, 13.65, 13.6, -0.003, 13.8, TODAY, P) == ('hold', 'stock_ok')


def test_stock_trailing_stop_from_peak():
    # peak 20 → 12% trail = 17.6; price 17.5 must sell even though above entry hard stop
    assert X.decide('CRGY', 99, 15.0, 17.5, 0.16, 20.0, TODAY, P) == ('sell', 'trailing_stop')


def test_stock_hard_stop_from_entry():
    assert X.decide('CRGY', 99, 13.65, 10.5, -0.23, 13.8, TODAY, P) == ('sell', 'hard_stop')


def test_option_hold_then_stop():
    assert X.decide(OPT, 15, 4.2, 3.2, -0.24, 0, TODAY, P) == ('hold', 'option_ok')
    assert X.decide(OPT, 15, 4.2, 2.4, -0.43, 0, TODAY, P) == ('sell', 'option_stop')


def test_option_take_profit_and_expiry_window():
    assert X.decide(OPT, 15, 4.2, 8.5, 1.02, 0, TODAY, P) == ('sell', 'option_take_profit')
    assert X.decide(OPT, 15, 4.2, 3.2, -0.24, 0, dt.date(2026, 11, 12), P) == ('sell', 'expiry_window')


def test_no_position_no_action():
    assert X.decide('CRGY', 0, 13.65, 13.6, 0, 13.8, TODAY, P) == ('hold', 'no_long')
