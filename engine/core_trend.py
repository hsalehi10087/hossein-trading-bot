"""موتور تصمیم خالص برای استراتژی «هسته با فیلتر روند» (QQQ ↔ IEF) + چک‌های ریسک آن."""
import datetime as dt
import math

import yaml

SCHEMA_KEYS = {'strategy', 'version', 'approved', 'stage', 'risk_asset', 'safe_asset', 'params', 'sizing', 'risk'}


def validate(r):
    errs = [f'missing {k}' for k in SCHEMA_KEYS if k not in r]
    if errs:
        return errs
    p, s, k = r['params'], r['sizing'], r['risk']
    if r['stage'] not in ('paper', 'live'):
        errs.append('stage must be paper or live')
    if not (50 <= int(p['sma_n']) <= 300):
        errs.append('sma_n out of range')
    if not (0 <= float(p['band']) <= 0.05):
        errs.append('band out of range')
    if not (0 < s['sleeve_usd'] <= k['max_position_usd']):
        errs.append('sleeve_usd must be positive and <= max_position_usd')
    for a in (r['risk_asset'], r['safe_asset']):
        if a not in k['allowed_symbols']:
            errs.append(f'{a} not in allowed_symbols')
    if not isinstance(k['kill_switch'], bool):
        errs.append('kill_switch must be bool')
    return errs


def load(path):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    errs = validate(r)
    if errs:
        raise ValueError('invalid rules: ' + '; '.join(errs))
    return r


def trend_states(close, n, band):
    """وضعیت روزبه‌روز با باند (هیسترزیس). هر روز فقط از داده‌ی همان روز و قبل از آن."""
    sma = close.rolling(n).mean()
    state, out = 0, []
    for p, s in zip(close, sma):
        why = 'hold_risk' if state else 'hold_safe'
        if s == s:  # not NaN
            if state == 0 and p > s * (1 + band):
                state, why = 1, 'enter_risk'
            elif state == 1 and p < s * (1 - band):
                state, why = 0, 'exit_to_safe'
        out.append((state, why))
    return out, sma


def decide(rules, close, prices, held, code_commit='local', dry_run=False):
    """close = سری بسته‌شدن روزانه‌ی risk_asset؛ prices/held = {symbol: price/qty}."""
    p, s = rules['params'], rules['sizing']
    ra, sa = rules['risk_asset'], rules['safe_asset']
    states, sma = trend_states(close, int(p['sma_n']), float(p['band']))
    sig, why = states[-1]
    active = ra if sig else sa
    target = {ra: 0, sa: 0}
    target[active] = int(s['sleeve_usd'] // prices[active])
    orders = []
    for sym in (sa, ra) if sig else (ra, sa):     # اول فروش، بعد خرید
        diff = target[sym] - held.get(sym, 0)
        thr = max(1, math.ceil(s['rebalance_min_frac'] * target[sym]))
        if diff < 0 and (target[sym] == 0 or -diff >= thr):
            orders.append({'symbol': sym, 'side': 'sell', 'qty': -diff, 'price': round(prices[sym], 2)})
        elif diff > 0 and (held.get(sym, 0) == 0 or diff >= thr):
            orders.append({'symbol': sym, 'side': 'buy', 'qty': diff, 'price': round(prices[sym], 2)})
    orders.sort(key=lambda o: o['side'] != 'sell')
    c, m = float(close.iloc[-1]), float(sma.iloc[-1])
    data_date = str(close.index[-1].date())
    did = f"{rules['strategy']}-v{rules['version']}-{data_date}" + ('-dry' if dry_run else '')
    return {
        'decision_id': did, 'strategy': rules['strategy'], 'rule_version': rules['version'],
        'code_commit': code_commit, 'data_date': data_date, 'symbol': active, 'signal': sig,
        'action': 'rebalance' if orders else 'none', 'qty': sum(o['qty'] for o in orders), 'dry_run': dry_run,
        'orders': orders,
        'reason': {'rule': why, 'close': round(c, 2), 'sma': round(m, 2), 'dist_pct': round((c / m - 1) * 100, 2),
                   'enter_above': round(m * (1 + float(p['band'])), 2), 'exit_below': round(m * (1 - float(p['band'])), 2),
                   'active': active, 'target': target, 'held': dict(held),
                   'params': {'sma_n': int(p['sma_n']), 'band': float(p['band'])}},
    }


def checks(rules, d, order, today, orders_today, already_ordered, kill_env=False, endpoint=''):
    k = rules['risk']
    usd = order['qty'] * order['price']
    pos_usd = d['reason']['target'].get(order['symbol'], 0) * order['price']
    age = (today - dt.date.fromisoformat(d['data_date'])).days
    kill = bool(k['kill_switch'] or kill_env)
    out = [('symbol_allowed', order['symbol'] in k['allowed_symbols'], order['symbol']),
           ('paper_only', rules['stage'] == 'paper' and 'paper-api' in endpoint, endpoint or 'unset'),
           ('kill_switch', not (kill and order['side'] == 'buy'), f'kill={kill}'),
           ('max_order_usd', usd <= k['max_order_usd'], f'{usd:.0f} <= {k["max_order_usd"]}'),
           ('max_position_usd', pos_usd <= k['max_position_usd'], f'{pos_usd:.0f} <= {k["max_position_usd"]}'),
           ('max_orders_per_day', orders_today < k['max_orders_per_day'], f'{orders_today} < {k["max_orders_per_day"]}'),
           ('not_duplicate', not already_ordered, order['symbol']),
           ('data_fresh', age <= k['max_data_age_days'], f'{age}d old')]
    return [{'name': n, 'passed': bool(p), 'detail': str(x)} for n, p, x in out]
