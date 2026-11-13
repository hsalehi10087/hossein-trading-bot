"""هماهنگ‌کننده‌ی سبد: قبل از هر سفارش، از طرف کل حساب اجازه می‌دهد، کوچک می‌کند یا رد می‌کند.

قوانین از rules/portfolio.yaml می‌آیند. همه‌ی ربات‌ها در یک اجرا از یک نمونه‌ی Conductor استفاده می‌کنند،
پس سفارش‌های تأییدشده‌ی هر ربات روی نقد و موقعیت‌های پیش‌بینی‌شده‌ی ربات بعدی اثر می‌گذارد.
"""
import json
import math
import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_PATH = os.path.join(ROOT, 'rules', 'portfolio.yaml')
OCC = re.compile(r'^([A-Z]{1,6})\d{6}[CP]\d{8}$')


def load(path=PORTFOLIO_PATH):
    r = yaml.safe_load(open(path, encoding='utf-8'))
    errs = validate(r)
    if errs:
        raise ValueError('invalid portfolio rules: ' + '; '.join(errs))
    r['_dynamic'] = load_dynamic(r)
    return r


def load_dynamic(r, store=None):
    """نمادهای پویا: رباتی که symbols_file دارد، فهرست سهم‌های خودش را در datastore نگه می‌دارد.
    خروجی {نماد: ربات}. فایل نبود یا خراب بود = فهرست خالی (سهم‌ها به صاحب پیش‌فرض برمی‌گردند)."""
    store = store or os.getenv('DATASTORE', os.path.join(ROOT, 'datastore'))
    out = {}
    for name, s in r['sleeves'].items():
        f = s.get('symbols_file')
        if not f:
            continue
        try:
            for sym in json.load(open(os.path.join(store, f), encoding='utf-8')):
                out.setdefault(str(sym), name)
        except Exception:
            pass
    return out


def claim(rules, symbol, sleeve):
    """ربات پویا یک سهم تازه را برای خودش ثبت می‌کند؛ فقط اگر صاحب صریح یا پویای دیگری نداشته باشد."""
    if OCC.match(symbol) or not rules['sleeves'].get(sleeve, {}).get('symbols_file'):
        return False
    for name, s in rules['sleeves'].items():
        if symbol in s['symbols']:
            return False
    dyn = rules.setdefault('_dynamic', {})
    if dyn.get(symbol, sleeve) != sleeve:
        return False
    dyn[symbol] = sleeve
    return True


def validate(r):
    errs = []
    for k in ('version', 'sleeves', 'account'):
        if k not in r:
            errs.append(f'missing {k}')
    if errs:
        return errs
    msp = r['account'].get('max_symbol_pct', 1.0)
    if not (0.05 <= msp <= 1.0):
        errs.append('account.max_symbol_pct out of range 0.05..1')
    owner = {}
    for name, s in r['sleeves'].items():
        for k in ('capital_usd', 'symbols', 'buys_allowed'):
            if k not in s:
                errs.append(f'{name}: missing {k}')
        sf = s.get('symbols_file')
        if sf is not None and (not isinstance(sf, str) or sf.startswith('/') or '..' in sf or not sf.endswith('.json')):
            errs.append(f'{name}: symbols_file must be a relative .json path inside the datastore')
        for sym in s.get('symbols', []):
            if sym == '*':
                if '*' in owner:
                    errs.append(f"wildcard '*' used by both {owner['*']} and {name}")
                owner['*'] = name
                continue
            if sym in owner:
                errs.append(f'{sym} owned by both {owner[sym]} and {name}')
            owner[sym] = name
    a = r['account']
    if not (0 < a.get('max_gross_exposure', 0) <= 1.0):
        errs.append('max_gross_exposure must be in (0, 1] (no leverage)')
    if not (0 < a.get('drawdown_brake', 0) < 1 and 0 < a.get('brake_scale', 0) <= 1):
        errs.append('brake settings out of range')
    return errs


def owner_of(rules, symbol):
    """صاحب نماد: اول فهرست صریح هر ربات؛ بعد رباتی که symbols: ['*'] دارد (فقط سهام، نه اختیار معامله)."""
    m = OCC.match(symbol)
    wild = None
    for name, s in rules['sleeves'].items():
        if symbol in s['symbols']:
            return name
        if m and m.group(1) in s.get('option_roots', []):
            return name
        if '*' in s['symbols']:
            wild = name
    if not m and symbol in rules.get('_dynamic', {}):      # سهم ثبت‌شده‌ی یک ربات پویا (قبل از صاحب پیش‌فرض)
        return rules['_dynamic'][symbol]
    return wild if wild and not m else None


class Conductor:
    def __init__(self, rules, account, positions, state_path, kill_env=False):
        self.r, self.a = rules, rules['account']
        self.equity = float(account['equity'])
        self.cash = float(account['cash'])
        self.pos = {p['symbol']: {'qty': float(p['qty']), 'price': float(p['current_price']),
                                  'value': float(p['market_value'])} for p in positions}
        self.state_path = state_path
        st = json.load(open(state_path)) if os.path.exists(state_path) else {}
        self.peak = max(float(st.get('peak_equity', 0)), self.equity)
        self.drawdown = self.equity / self.peak - 1
        self.brake = self.drawdown <= -self.a['drawdown_brake']
        self.kill = bool(self.a['kill_switch'] or kill_env)
        self.log = []

    # ---- وضعیت ----
    def sleeve_value(self, sleeve):
        return sum(v['value'] for s, v in self.pos.items() if owner_of(self.r, s) == sleeve)

    def gross(self):
        return sum(abs(v['value']) for v in self.pos.values())

    def orphans(self):
        """موقعیت‌هایی که هیچ رباتی صاحبشان نیست."""
        return [(s, v['qty'], round(v['value'], 2)) for s, v in self.pos.items() if owner_of(self.r, s) is None]

    def save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        json.dump({'peak_equity': round(self.peak, 2), 'last_equity': round(self.equity, 2),
                   'drawdown': round(self.drawdown, 4), 'brake': self.brake}, open(self.state_path, 'w'), indent=1)

    # ---- اجازه ----
    def approve(self, sleeve, order):
        """order = {symbol, side, qty, price}. خروجی: (qty تأییدشده، چک‌ها). فروش کوچک نمی‌شود؛ خرید ممکن است کوچک شود."""
        sym, side, qty, px = order['symbol'], order['side'], int(order['qty']), float(order['price'])
        s = self.r['sleeves'].get(sleeve)
        checks = [('sleeve_known', s is not None, sleeve),
                  ('symbol_owner', s is not None and owner_of(self.r, sym) == sleeve, f'{sym} → {owner_of(self.r, sym)}')]
        if side == 'sell':
            held = self.pos.get(sym, {}).get('qty', 0)
            checks.append(('no_short', qty <= held, f'{qty} <= {held:g}'))
            ok = all(c[1] for c in checks)
            if ok:
                self._apply(sym, -qty, px)
            return self._done(sleeve, order, qty if ok else 0, checks)
        scale = self.a['brake_scale'] if self.brake else 1.0
        cap = s['capital_usd'] * scale * (1 + self.a['sleeve_drift_tolerance']) if s else 0
        room_sleeve = cap - self.sleeve_value(sleeve)
        room_cash = self.cash - self.a['reserve_cash_usd'] if self.a['no_margin'] else float('inf')
        room_gross = self.a['max_gross_exposure'] * self.equity - self.gross()
        room_symbol = self.a.get('max_symbol_pct', 1.0) * self.equity - self.pos.get(sym, {}).get('value', 0.0)
        room = min(room_sleeve, room_cash, room_gross, room_symbol)
        fit = max(0, min(qty, math.floor(room / px))) if px > 0 else 0
        checks += [('buys_allowed', bool(s and s['buys_allowed']), str(s and s['buys_allowed'])),
                   ('kill_switch', not self.kill, f'kill={self.kill}'),
                   ('sleeve_budget', room_sleeve >= px, f'room {room_sleeve:,.0f}$ (brake={self.brake})'),
                   ('cash_no_margin', room_cash >= px, f'room {room_cash:,.0f}$'),
                   ('gross_exposure', room_gross >= px, f'room {room_gross:,.0f}$'),
                   ('symbol_cap', room_symbol >= px, f'room {room_symbol:,.0f}$ (max {self.a.get("max_symbol_pct", 1.0):.0%} of equity per symbol)'),
                   ('not_blocked', sym not in (self.a.get('blocked_symbols') or []), sym),
                   ('no_options', not (OCC.match(sym) and self.a.get('options_blocked', False)), sym),
                   ('fits', fit >= 1, f'{fit}/{qty}')]
        ok = all(c[1] for c in checks)
        q = fit if ok else 0
        if q:
            self._apply(sym, q, px)
        return self._done(sleeve, order, q, checks)

    def _apply(self, sym, dq, px):
        p = self.pos.setdefault(sym, {'qty': 0.0, 'price': px, 'value': 0.0})
        p['qty'] += dq
        p['value'] = p['qty'] * px
        self.cash -= dq * px

    def _done(self, sleeve, order, q, checks):
        cks = [{'name': f'portfolio:{n}', 'passed': bool(p), 'detail': str(d)} for n, p, d in checks]
        self.log.append({'sleeve': sleeve, **order, 'approved_qty': q})
        return q, cks
