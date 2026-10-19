"""بارگذاری و اعتبارسنجی فایل قوانین YAML."""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(ROOT, 'rules', 'gld_trend.yaml')

SCHEMA = {
    'strategy': str, 'version': int, 'approved': str, 'stage': str, 'signal_symbol': str, 'trade_symbol': str,
    'params': {'n_entry': int, 'm_exit': int, 'atr_k': (int, float), 'atr_n': int, 'vol_window': int,
               'target_vol': (int, float)},
    'sizing': {'sleeve_usd': (int, float), 'rebalance_min_frac': (int, float)},
    'risk': {'allowed_symbols': list, 'max_position_usd': (int, float), 'max_order_usd': (int, float),
             'max_orders_per_day': int, 'max_data_age_days': int, 'kill_switch': bool},
}


def _check(obj, schema, path=''):
    errs = []
    for k, t in schema.items():
        if k not in obj:
            errs.append(f'missing {path}{k}')
        elif isinstance(t, dict):
            errs += _check(obj[k], t, f'{path}{k}.') if isinstance(obj[k], dict) else [f'{path}{k} must be a map']
        elif not isinstance(obj[k], t) or (t is int and isinstance(obj[k], bool)):
            errs.append(f'{path}{k} has wrong type {type(obj[k]).__name__}')
    return errs


def validate(r):
    errs = _check(r, SCHEMA)
    if errs:
        return errs
    p, s, k = r['params'], r['sizing'], r['risk']
    if r['stage'] not in ('paper', 'live'):
        errs.append('stage must be paper or live')
    if not (2 <= p['m_exit'] <= p['n_entry'] <= 250):
        errs.append('need 2 <= m_exit <= n_entry <= 250')
    if not (0 <= p['atr_k'] <= 10):
        errs.append('atr_k out of range')
    if not (0 < p['target_vol'] <= 0.5):
        errs.append('target_vol out of range')
    if not (0 < s['sleeve_usd'] <= k['max_position_usd']):
        errs.append('sleeve_usd must be positive and <= max_position_usd')
    if r['trade_symbol'] not in k['allowed_symbols']:
        errs.append('trade_symbol not in allowed_symbols')
    return errs


def load(path=RULES_PATH):
    with open(path, encoding='utf-8') as f:
        r = yaml.safe_load(f)
    errs = validate(r)
    if errs:
        raise ValueError('invalid rules: ' + '; '.join(errs))
    return r
