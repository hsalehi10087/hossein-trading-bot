"""تصمیم خروج خالص برای موقعیت‌های بلندمدت (سهام و اختیار خرید). هیچ خریدی برنمی‌گرداند.

decide(...) → (action, reason). action = 'sell' | 'hold'.
"""
import datetime as dt
import re

OCC = re.compile(r'^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$')


def is_option(symbol):
    return OCC.match(symbol) is not None


def expiry(symbol):
    m = OCC.match(symbol)
    if not m:
        return None
    return dt.date(2000 + int(m.group(2)), int(m.group(3)), int(m.group(4)))


def decide(symbol, qty, avg, price, plpc, peak, today, p):
    """qty: تعداد موقعیت؛ avg: میانگین خرید؛ price: قیمت فعلی؛ plpc: سود/زیان باز به نسبت؛
    peak: بالاترین قیمت دیده‌شده از زمان ورود (برای سهام)؛ today: تاریخ امروز؛ p: پارامترهای rules/exit_only.yaml."""
    if qty <= 0:
        return 'hold', 'no_long'
    if is_option(symbol):
        if plpc <= -p['option_stop']:
            return 'sell', 'option_stop'
        if plpc >= p['option_take_profit']:
            return 'sell', 'option_take_profit'
        exp = expiry(symbol)
        if exp and (exp - today).days <= p['option_exit_days']:
            return 'sell', 'expiry_window'
        return 'hold', 'option_ok'
    if price <= avg * (1 - p['hard_stop_stock']):
        return 'sell', 'hard_stop'
    if price <= max(peak, avg) * (1 - p['trail_stock']):
        return 'sell', 'trailing_stop'
    return 'hold', 'stock_ok'
