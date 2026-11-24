import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import option_bot as O  # noqa: E402

R = O.load_rules()
T = dt.date(2026, 11, 24)


def up(n=260, step=0.3):
    return [100 + i * step for i in range(n)]


def test_parse_occ():
    o = O.parse_occ('AAPL261218C00255000')
    assert o['root'] == 'AAPL' and o['type'] == 'C' and o['strike'] == 255.0 and o['expiry'] == dt.date(2026, 12, 18)
    assert O.parse_occ('garbage') is None


def test_pick_strategy_trend_and_short_history():
    assert O.pick_strategy([100] * 50)[0] is None
    s, why = O.pick_strategy(up() + [up()[-1] - 2, up()[-1] - 4])    # صعودی + اصلاح
    assert s in ('bull_put_credit', 'bull_call_debit') and 'RSI' in why
    down = [200 - i * 0.3 for i in range(260)]
    assert O.pick_strategy(down + [down[-1] + 1, down[-1] + 2])[0] in ('bear_call_credit', None)


def snap(bp, ap, d):
    return {'latestQuote': {'bp': bp, 'ap': ap}, 'greeks': {'delta': d}}


def test_bull_put_credit_spread_math():
    exp = T + dt.timedelta(days=36)
    ds = exp.strftime('%y%m%d')
    chain = {f'XYZ{ds}P00095000': snap(1.9, 2.0, -0.27), f'XYZ{ds}P00092000': snap(0.8, 0.9, -0.15),
             f'XYZ{ds}P00090000': snap(0.4, 0.5, -0.09)}
    sp = O.build_spread('bull_put_credit', chain, T, R)
    assert sp and sp['sell_a'] and sp['leg_a'].endswith('P00095000')
    assert abs(sp['entry_net'] - (1.95 - 0.85)) < 1e-6 and abs(sp['max_loss'] - (3 - 1.1)) < 1e-6
    assert O.qty_for(sp, R) == int(R['risk_per_trade_usd'] // (sp['max_loss'] * 100))


def test_illiquid_rejected():
    exp = T + dt.timedelta(days=36)
    ds = exp.strftime('%y%m%d')
    chain = {f'XYZ{ds}P00095000': snap(0.5, 2.0, -0.27), f'XYZ{ds}P00092000': snap(0.8, 0.9, -0.15)}
    assert O.build_spread('bull_put_credit', chain, T, R) is None


def test_exits_credit_and_debit():
    c = {'sell_a': True, 'entry_net': 1.0, 'expiry': str(T + dt.timedelta(days=30))}
    assert O.exit_reason(c, 0.5, T, R).startswith('سود')
    assert O.exit_reason(c, 2.0, T, R).startswith('حد ضرر')
    assert O.exit_reason(c, 0.8, T, R) is None
    d = {'sell_a': False, 'entry_net': 2.0, 'expiry': str(T + dt.timedelta(days=30))}
    assert O.exit_reason(d, 3.3, T, R).startswith('سود') and O.exit_reason(d, 0.9, T, R).startswith('حد ضرر')
    near = {**d, 'expiry': str(T + dt.timedelta(days=5))}
    assert 'سررسید' in O.exit_reason(near, 2.0, T, R)
    assert O.pnl_per_share(c, 0.5) == 0.5 and O.pnl_per_share(d, 3.0) == 1.0
