import numpy as np
import pandas as pd

import risk_report as RR


def test_analyse_and_text():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.01, 260)
    idx = pd.bdate_range('2025-01-01', periods=260)
    px = pd.DataFrame({'qqq_trend': 100 * np.cumprod(1 + base),
                       'qqq_200': 100 * np.cumprod(1 + base + rng.normal(0, 0.001, 260)),
                       'gld_trend': 100 * np.cumprod(1 + rng.normal(0, 0.008, 260))}, index=idx)
    w = {'qqq_trend': 20000, 'qqq_200': 5000, 'gld_trend': 10000}
    res = RR.analyse(px, w)
    assert res['pairs'][0][:2] == ('qqq_trend', 'qqq_200') and res['pairs'][0][2] > 0.95
    assert -1 < res['max_dd'] <= 0 and res['var95_1d'] > 0
    assert abs(sum(res['risk_share'].values()) - 1) < 1e-9
    t = RR.text(res, w, [('QQQ', 0.21)])
    assert 'هم‌جهت' in t and 'QQQ' in t
