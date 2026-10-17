"""
Options overlay for the 1H strategy (Alpaca paper) - LONG CALLS ONLY (defined risk).
Goal: add leveraged upside on the strongest momentum names while the maximum loss stays
limited to the premium paid. It does NOT guarantee profit and is not backtested.

Entry (all must hold)
- 1H regime is BULL and price is above its 20-day average (daily trend filter)
- No earnings in the next 7 days (avoids IV crush); if the lookup fails -> skip
- Max 4 open option positions; total premium <= OPT_BUDGET_PCT of equity (default 10%)
- Strongest recent momentum gets the largest share of the budget
Contract
- Call, 30-60 days to expiry, delta 0.55-0.70 (fallback: strike ~2% in the money)
- Open interest >= 100 and bid/ask spread <= 12% of mid; limit order at mid
Exit
- Sell half at +60%, trail the rest 25% below its peak, hard stop at -35%, close at <= 7 days to expiry
Env: OPTIONS_ENABLED=0 disables; OPT_BUDGET_PCT overrides the premium budget.
"""
import os
import re
import datetime as dt
import requests

OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
HALF_TP, TRAIL, STOP, MIN_DTE_EXIT = 0.60, 0.25, -0.35, 7
DTE_MIN, DTE_MAX = 30, 60
D_LO, D_HI = 0.55, 0.70
MIN_OI, MAX_SPREAD, MAX_POS = 100, 0.12, 4
EARN_DAYS = 7


def parse_occ(sym):
    m = OCC.match(sym or "")
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    return {"root": root, "exp": dt.datetime.strptime(ymd, "%y%m%d").date(), "type": cp,
            "strike": int(strike) / 1000}


def dte(sym, today=None):
    p = parse_occ(sym)
    return None if not p else (p["exp"] - (today or dt.date.today())).days


def momentum(closes, n=20):
    return 0.0 if len(closes) < 2 else closes[-1] / closes[-min(n, len(closes))] - 1


def trend_ok(daily_closes, spot):
    return len(daily_closes) >= 20 and spot > sum(daily_closes[-20:]) / 20


def pick_contract(contracts, snaps, spot, today=None):
    """snaps: {symbol: {'bp','ap','delta'}}. Returns (symbol, mid, delta) or None."""
    today = today or dt.date.today()
    best = None
    for c in contracts:
        sym = c.get("symbol")
        p = parse_occ(sym)
        if not p or p["type"] != "C" or c.get("tradable") is False:
            continue
        if not DTE_MIN <= (p["exp"] - today).days <= DTE_MAX:
            continue
        if int(float(c.get("open_interest") or 0)) < MIN_OI:
            continue
        q = snaps.get(sym) or {}
        bp, ap = q.get("bp") or 0, q.get("ap") or 0
        if bp <= 0 or ap < bp:
            continue
        mid = (bp + ap) / 2
        if (ap - bp) / mid > MAX_SPREAD:
            continue
        d = q.get("delta")
        if d is not None:
            if not D_LO <= d <= D_HI:
                continue
            score = abs(d - 0.62)
        else:                                   # no greeks: ~2% in the money
            score = abs(p["strike"] - spot * 0.98) / spot + 0.5
        if best is None or score < best[0]:
            best = (score, sym, round(mid, 2), d)
    return None if best is None else best[1:]


def exit_action(cost, price, peak, days_left, half_sold):
    """Returns 'HALF', 'TRAIL', 'STOP', 'EXPIRY' or None."""
    pl = price / cost - 1 if cost else 0
    if pl <= STOP:
        return "STOP"
    if days_left is not None and days_left <= MIN_DTE_EXIT:
        return "EXPIRY"
    if half_sold and price <= peak * (1 - TRAIL):
        return "TRAIL"
    if not half_sold and pl >= HALF_TP:
        return "HALF"
    return None


def earnings_soon(symbol, finnhub_key, today=None):
    """True if earnings fall within EARN_DAYS (or the lookup fails: be safe and skip)."""
    today = today or dt.date.today()
    try:
        r = requests.get("https://finnhub.io/api/v1/calendar/earnings",
                         params={"from": str(today), "to": str(today + dt.timedelta(days=EARN_DAYS)),
                                 "symbol": symbol, "token": finnhub_key}, timeout=10)
        if r.status_code != 200:
            return True
        return bool(r.json().get("earningsCalendar"))
    except Exception:
        return True


def snapshots(symbols, key, secret):
    out = {}
    try:
        for i in range(0, len(symbols), 100):
            r = requests.get("https://data.alpaca.markets/v1beta1/options/snapshots",
                             headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                             params={"symbols": ",".join(symbols[i:i + 100]), "feed": "indicative"}, timeout=20)
            if r.status_code != 200:
                continue
            for s, v in (r.json().get("snapshots") or {}).items():
                q = v.get("latestQuote") or {}
                out[s] = {"bp": q.get("bp"), "ap": q.get("ap"), "delta": (v.get("greeks") or {}).get("delta")}
    except Exception:
        pass
    return out


def contracts_for(alpaca, underlying, today=None):
    today = today or dt.date.today()
    q = (f"/options/contracts?underlying_symbols={underlying}&type=call&status=active&limit=300"
         f"&expiration_date_gte={today + dt.timedelta(days=DTE_MIN)}"
         f"&expiration_date_lte={today + dt.timedelta(days=DTE_MAX)}")
    return (alpaca.get(q) or {}).get("option_contracts", [])


def run(alpaca, symbols, regimes, prices, hourly, daily, equity, state, key, secret, finnhub_key,
        dry=False, log=print):
    """Manage option positions. `state` is mutated: state['_opt'][contract] = {'peak','half'}.
    Returns signal strings; never raises."""
    signals = []
    if os.environ.get("OPTIONS_ENABLED", "1") == "0":
        return signals
    try:
        opt_state = state.setdefault("_opt", {})
        total = equity * float(os.environ.get("OPT_BUDGET_PCT", "0.10"))
        positions = [p for p in alpaca.get_positions() if p.get("asset_class") == "us_option"]
        mine = [p for p in positions if (parse_occ(p["symbol"]) or {}).get("root") in symbols]
        used, roots = 0.0, set()

        for p in mine:                                                  # ---- exits
            sym, qty = p["symbol"], int(float(p["qty"]))
            cost, cur = float(p["avg_entry_price"]), float(p.get("current_price") or p["avg_entry_price"])
            root = parse_occ(sym)["root"]
            st = opt_state.setdefault(sym, {"peak": cur, "half": False})
            st["peak"] = max(st["peak"], cur)
            roots.add(root)
            used += cost * qty * 100
            act = exit_action(cost, cur, st["peak"], dte(sym), st["half"])
            if not act or qty < 1:
                continue
            sell_qty = max(1, qty // 2) if act == "HALF" and qty > 1 else qty
            ok = dry or alpaca.post("/orders", {"symbol": sym, "qty": sell_qty, "side": "sell",
                                                "type": "limit", "limit_price": round(cur, 2),
                                                "time_in_force": "day"})
            if ok:
                if act == "HALF":
                    st["half"] = True
                else:
                    opt_state.pop(sym, None)
                    roots.discard(root)
                signals.append(f"💼 OPT {act} {sym}: {sell_qty} @ ${cur:.2f} | {cur / cost - 1:+.0%}")
        live = {p["symbol"] for p in positions}
        for sym in [s for s in opt_state if s not in live]:
            opt_state.pop(sym, None)                                    # closed elsewhere

        open_n = len(mine)                                              # ---- entries
        ranked = sorted((s for s in symbols if s not in roots and regimes.get(s) is not None
                         and regimes[s].name == "BULL" and prices.get(s)),
                        key=lambda s: momentum(hourly.get(s, [])), reverse=True)
        cands = []
        for s in ranked:
            if open_n + len(cands) >= MAX_POS:
                break
            if not trend_ok(daily.get(s, []), prices[s]):
                log(f"  {s}: below 20-day average - options skipped")
                continue
            if earnings_soon(s, finnhub_key):
                log(f"  {s}: earnings within {EARN_DAYS} days (or unknown) - options skipped")
                continue
            cands.append(s)
        weights = [len(cands) - i for i in range(len(cands))]           # strongest gets the most
        for s, w in zip(cands, weights):
            room = total - used
            if room <= 0:
                break
            share = total * w / max(1, sum(weights))
            cs = contracts_for(alpaca, s)
            pick = pick_contract(cs, snapshots([c["symbol"] for c in cs], key, secret), prices[s])
            if not pick:
                log(f"  {s}: no liquid call matching delta/expiry - skipped")
                continue
            sym, mid, delta = pick
            qty = int(min(share, room) // (mid * 100))
            if qty < 1:
                log(f"  {s}: contract ${mid * 100:.0f} exceeds its budget - skipped")
                continue
            ok = dry or alpaca.post("/orders", {"symbol": sym, "qty": qty, "side": "buy", "type": "limit",
                                                "limit_price": mid, "time_in_force": "day"})
            if ok:
                used += mid * qty * 100
                opt_state[sym] = {"peak": mid, "half": False}
                d = f" Δ{delta:.2f}" if delta is not None else ""
                signals.append(f"💼 OPT BUY_CALL {sym}: {qty} @ ${mid:.2f}{d} (premium ${mid * qty * 100:,.0f})")
    except Exception as e:                                              # never stop the stock logic
        log(f"⚠️ options overlay error: {e}")
    return signals
