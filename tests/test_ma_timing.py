import ma_timing as M
from engine import conductor as PC


def test_rules_and_owners():
    r = M.load_rules()
    assert {s['sleeve'] for s in r['strategies']} == {'dbmf_band', 'spy_10m', 'qqq_200'}
    R = PC.load()
    assert PC.owner_of(R, 'DBMF') == 'dbmf_band' and PC.owner_of(R, 'VOO') == 'spy_10m' and PC.owner_of(R, 'QQQM') == 'qqq_200'
    assert PC.owner_of(R, 'QQQ') == 'qqq_trend'


def test_regime_band_hysteresis():
    flat = [100.0] * 210
    up = flat + [101.0] * 3            # ۱٪ بالای میانگین: با باند ۲٪ هنوز بیرون
    assert M.regime(up, 200, 0.02, 0.02)[0] is False
    assert M.regime(flat + [103.0] * 3, 200, 0.02, 0.02)[0] is True
    back = flat + [103.0] * 3 + [99.5] * 2   # بین دو باند: داخل می‌ماند
    assert M.regime(back, 200, 0.02, 0.02)[0] is True
    assert M.regime(flat + [103.0] * 3 + [97.0], 200, 0.02, 0.02)[0] is False


def test_regime_plain_cross():
    assert M.regime([100.0] * 210 + [100.5], 200, 0, 0)[0] is True
    assert M.regime([100.0] * 210 + [100.5, 99.5], 200, 0, 0)[0] is False
