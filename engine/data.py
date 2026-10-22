"""داده‌ی مشترک: قیمت‌های روزانه از Alpaca (فید SIP، تعدیل‌شده) و تاریخ آخرین جلسه‌ی کامل بازار.

قانون تازگی: اگر آخرین کندل داده از آخرین جلسه‌ی بسته‌شده‌ی بازار قدیمی‌تر باشد، ربات تصمیم نمی‌گیرد.
"""
import datetime as dt
import os

import pandas as pd
import requests

DATA = 'https://data.alpaca.markets/v2'


def _h():
    return {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}


def last_closed_session(trading_base, now_utc=None):
    """آخرین روز معاملاتی که دست‌کم ۱۵ دقیقه از بسته‌شدنش گذشته (از تقویم Alpaca، به وقت نیویورک)."""
    from zoneinfo import ZoneInfo
    ny_tz = ZoneInfo('America/New_York')
    now_ny = (now_utc or dt.datetime.now(dt.timezone.utc)).astimezone(ny_tz)
    start = (now_ny - dt.timedelta(days=10)).date().isoformat()
    r = requests.get(trading_base.rstrip('/') + '/calendar', headers=_h(),
                     params={'start': start, 'end': now_ny.date().isoformat()}, timeout=20)
    r.raise_for_status()
    best = None
    for d in r.json():
        h, m = map(int, d['close'].split(':'))
        closed = dt.datetime.combine(dt.date.fromisoformat(d['date']), dt.time(h, m), ny_tz)
        if now_ny >= closed + dt.timedelta(minutes=15):
            best = closed.date()
    return best


def daily_closes(symbols, start='2005-01-01', feed='sip'):
    """بسته‌شدن‌های روزانه‌ی تعدیل‌شده (سود نقدی و تقسیم) برای چند نماد → DataFrame."""
    out, token = {}, None
    for _ in range(200):
        params = {'symbols': ','.join(symbols), 'timeframe': '1Day', 'start': start, 'limit': 10000,
                  'adjustment': 'all', 'feed': feed, 'sort': 'asc'}
        if token:
            params['page_token'] = token
        r = requests.get(DATA + '/stocks/bars', headers=_h(), params=params, timeout=30)
        if r.status_code == 403 and feed == 'sip':
            return daily_closes(symbols, start, 'iex')
        r.raise_for_status()
        j = r.json()
        for s, bars in (j.get('bars') or {}).items():
            out.setdefault(s, []).extend((b['t'][:10], b['c']) for b in bars)
        token = j.get('next_page_token')
        if not token:
            break
    cols = {s: pd.Series({pd.Timestamp(d): c for d, c in v}) for s, v in out.items()}
    return pd.DataFrame(cols).sort_index()


def is_fresh(last_bar_date, session):
    return session is None or last_bar_date >= session
