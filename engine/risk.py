"""چک‌های ریسک مستقل از منطق سیگنال. هر چک {name, passed, detail} برمی‌گرداند؛ یک رد = بدون سفارش."""
import datetime as dt


def checks(rules, d, today, orders_today, already_ordered, kill_env=False, endpoint=''):
    k = rules['risk']
    if d['action'] == 'none':
        return []
    px, qty = d['reason']['gld_price'], d['qty']
    order_usd = px * qty
    new_pos_usd = px * d['reason']['target_qty']
    age = (today - dt.date.fromisoformat(d['data_date'])).days
    kill = bool(k['kill_switch'] or kill_env)
    out = [
        ('symbol_allowed', d['symbol'] in k['allowed_symbols'], d['symbol']),
        ('paper_only', rules['stage'] == 'paper' and 'paper-api' in endpoint, endpoint or 'unset'),
        ('kill_switch', not (kill and d['action'] == 'buy'), f'kill={kill}'),
        ('max_order_usd', order_usd <= k['max_order_usd'], f'{order_usd:.0f} <= {k["max_order_usd"]}'),
        ('max_position_usd', new_pos_usd <= k['max_position_usd'], f'{new_pos_usd:.0f} <= {k["max_position_usd"]}'),
        ('max_orders_per_day', orders_today < k['max_orders_per_day'], f'{orders_today} < {k["max_orders_per_day"]}'),
        ('not_duplicate', not already_ordered, d['decision_id']),
        ('data_fresh', age <= k['max_data_age_days'], f'{age}d old'),
    ]
    return [{'name': n, 'passed': bool(p), 'detail': str(x)} for n, p, x in out]
