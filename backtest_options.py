"""
Backtest of the options-overlay rules with optopsy (https://github.com/hsalehi10087/optopsy).
Usage:  python backtest_options.py chains.csv [--symbol MU]
Needs a REAL historical options chain CSV (quote_date, expiration, strike, type, bid, ask, delta).
Source: `pip install optopsy[data]` + `optopsy-data download MU NVDA` (needs EODHD_API_KEY), or any vendor CSV.
Rules mirrored from options_overlay.py: long calls, delta 0.55-0.70, 30-60 DTE entry, exit at 7 DTE,
take profit +60%, stop -35%. (The live bot sells half at +60% and trails the rest; optopsy exits all at +60%
-> this backtest is slightly conservative on winners.)
"""
import json
import os
import sys
import optopsy as op

def run(path, symbol=None):
    # default column indices = optopsy sample layout; override with COLS='{"bid": 5, ...}'
    cols = dict(underlying_symbol=0, option_type=5, expiration=6, quote_date=7, strike=8, bid=10, ask=11, delta=15)
    cols.update(json.loads(os.environ.get("COLS", "{}")))
    data = op.csv_data(path, **cols)
    if symbol:
        data = data[data["underlying_symbol"] == symbol]
    res = op.simulate(data, op.long_calls, capital=100_000, quantity=1, max_positions=4,
                      selector="nearest", max_entry_dte=60, exit_dte=7,
                      leg1_delta={"target": 0.62, "min": 0.55, "max": 0.70},
                      take_profit=0.60, stop_loss=-0.35)
    print(res.summary)
    print(res.trade_log.tail(10))
    return res

if __name__ == "__main__":
    a = sys.argv[1:]
    run(a[0], a[a.index("--symbol") + 1] if "--symbol" in a else None)
