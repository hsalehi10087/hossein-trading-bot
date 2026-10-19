import copy
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from engine import rules as R
from engine.decide import decide, trend_states
from engine.risk import checks


def synth(n=900, seed=7):
    rng = np.random.default_rng(seed)
    close = 1500 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    hi = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    lo = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({'High': hi, 'Low': lo, 'Close': close}, index=pd.bdate_range('2020-01-01', periods=n))


RULES = R.load()
GOLDEN_ENTRIES = 10  # تغییر منطق سیگنال این عدد را عوض می‌کند و باید آگاهانه در PR به‌روز شود


def test_rules_file_is_valid():
    assert RULES['stage'] == 'paper'
    assert R.validate(RULES) == []


def test_invalid_rules_rejected():
    bad = copy.deepcopy(RULES)
    bad['params']['m_exit'] = bad['params']['n_entry'] + 1
    assert R.validate(bad)
    bad = copy.deepcopy(RULES)
    bad['trade_symbol'] = 'TSLA'
    assert R.validate(bad)


def test_no_lookahead():
    df = synth()
    p = RULES['params']
    full = trend_states(df, p['n_entry'], p['m_exit'], p['atr_k'])[0]
    for i in range(120, len(df), 37):
        part = trend_states(df.iloc[:i + 1], p['n_entry'], p['m_exit'], p['atr_k'])[0]
        assert part.state.iloc[-1] == full.state.iloc[i]
        assert part.why.iloc[-1] == full.why.iloc[i]


def test_decide_deterministic_and_complete():
    df = synth()
    a = decide(RULES, df, 180.0, 0, 'abc')
    b = decide(RULES, df, 180.0, 0, 'abc')
    assert a == b
    for key in ('decision_id', 'rule_version', 'code_commit', 'reason'):
        assert key in a
    assert a['reason']['rule'] in ('entry_breakout', 'exit_channel', 'exit_atr_stop', 'hold_in', 'hold_out')


def test_golden_trades():
    df = synth()
    st = trend_states(df, 40, 10, 2.0)[0]
    entries = int(((st.state == 1) & (st.state.shift(1, fill_value=0) == 0)).sum())
    assert entries == GOLDEN_ENTRIES


def test_exit_when_out_of_market_and_holding():
    df = synth()
    df.loc[df.index[-15:], 'Close'] *= np.linspace(1, 0.8, 15)   # افت شدید ← خارج از بازار
    df['Low'] = np.minimum(df.Low, df.Close)
    d = decide(RULES, df, 180.0, 30, 'x')
    assert d['signal'] == 0 and d['action'] == 'sell' and d['qty'] == 30


def _d(action='buy', sym='GLD', qty=10, target=10):
    return {'action': action, 'symbol': sym, 'qty': qty, 'decision_id': 'x', 'data_date': '2026-10-19',
            'reason': {'gld_price': 380.0, 'target_qty': target}}


EP = 'https://paper-api.alpaca.markets/v2'
TODAY = dt.date(2026, 10, 20)


def failed(*a, **k):
    return {c['name'] for c in checks(*a, **k) if not c['passed']}


def test_risk_passes_normal_order():
    assert failed(RULES, _d(), TODAY, 0, False, False, EP) == set()


def test_risk_blocks():
    assert 'symbol_allowed' in failed(RULES, _d(sym='TSLA'), TODAY, 0, False, False, EP)
    assert 'paper_only' in failed(RULES, _d(), TODAY, 0, False, False, 'https://api.alpaca.markets/v2')
    assert 'max_order_usd' in failed(RULES, _d(qty=100, target=100), TODAY, 0, False, False, EP)
    assert 'max_orders_per_day' in failed(RULES, _d(), TODAY, 1, False, False, EP)
    assert 'not_duplicate' in failed(RULES, _d(), TODAY, 0, True, False, EP)
    assert 'data_fresh' in failed(RULES, _d(), dt.date(2026, 11, 1), 0, False, False, EP)


def test_kill_switch_blocks_buys_not_sells():
    assert 'kill_switch' in failed(RULES, _d('buy'), TODAY, 0, False, True, EP)
    assert 'kill_switch' not in failed(RULES, _d('sell', target=0), TODAY, 0, False, True, EP)


def test_audit_roundtrip(tmp_path):
    from engine.audit import Audit
    a = Audit(str(tmp_path / 'a.sqlite'))
    d = decide(RULES, synth(), 180.0, 0, 'abc')
    a.record_decision(d, [], 'no_action')
    assert a.unique_id(d['decision_id']).endswith('-rerun2')
    a.record_order('c1', d['decision_id'], 'GLD', 'buy', 10, 'submitted', 'b1', 'ok')
    a.record_fill('c1', 'filled', 10, 100.0, '2026-10-20T13:30:00Z')
    a.record_order('c2', d['decision_id'], 'GLD', 'sell', 10, 'submitted', 'b2', 'ok')
    a.record_fill('c2', 'filled', 10, 110.0, '2026-10-21T13:30:00Z')
    assert a.realized_pl('GLD') == 100.0
