"""معامله‌ی آزمایشی (Alpaca paper) استراتژی روند طلا روی GLD.

سیگنال از داده‌ی روزانه‌ی طلا (GC=F) — همان داده‌ای که بک‌تست شد:
  ورود: بسته‌شدن بالای سقف ۴۰ روزه | خروج: بسته‌شدن زیر کف ۱۰ روزه یا حد ضرر شناور ۲×ATR(14)
  اندازه: سهمیه‌ی ثابت (GLD_SLEEVE_USD، پیش‌فرض ۱۰٬۰۰۰ دلار) × min(1, 12٪ / نوسان ۲۰ روزه‌ی سالانه)
فقط موقعیت GLD را مدیریت می‌کند و به معاملات دیگر حساب دست نمی‌زند.
DRY_RUN=true → سفارشی ثبت نمی‌شود، فقط گزارش.
"""
import csv
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

N_ENTRY, M_EXIT, ATR_K, TARGET_VOL = 40, 10, 2.0, 0.12
SLEEVE = float(os.getenv('GLD_SLEEVE_USD', '10000'))
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
BASE = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY', ''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET', '')}
LOG = os.path.join('data', 'gld_trend_log.csv')


def flat(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[['High', 'Low', 'Close']].dropna()


def atr(df, n=14):
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(), (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def signal(df):
    hi, lo, a = df.High.rolling(N_ENTRY).max().shift(), df.Low.rolling(M_EXIT).min().shift(), atr(df)
    cur, peak = 0, 0.0
    for c, h, l, av in zip(df.Close, hi, lo, a):
        if cur == 0 and c > h:
            cur, peak = 1, c
        elif cur == 1:
            peak = max(peak, c)
            if c < l or c < peak - ATR_K * av:
                cur = 0
    return cur, peak, float(a.iloc[-1])


def alpaca(method, path, **kw):
    r = requests.request(method, BASE + path, headers=H, timeout=20, **kw)
    return r


def held_qty():
    r = alpaca('GET', '/positions/GLD')
    if r.status_code == 404:
        return 0
    r.raise_for_status()
    return int(float(r.json()['qty']))


def tg(text):
    tok, chats = os.getenv('TELEGRAM_BOT_TOKEN', ''), [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip()]
    for c in chats:
        try:
            requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'}, timeout=20)
        except Exception as e:
            print('telegram error', e, file=sys.stderr)


def main():
    g = flat(yf.download('GC=F', start='2008-01-01', interval='1d', progress=False, auto_adjust=False))
    gld = flat(yf.download('GLD', period='1mo', interval='1d', progress=False, auto_adjust=False))
    sig, peak, a14 = signal(g)
    r = g.Close.pct_change()
    vol = float(r.tail(20).std() * math.sqrt(252))
    size = min(1.0, TARGET_VOL / vol) if vol > 0 else 0.0
    px = float(gld.Close.iloc[-1])
    target = int(SLEEVE * sig * size // px)
    cur = held_qty()
    diff = target - cur
    entry_lvl = float(g.High.tail(N_ENTRY).max())
    exit_lvl = float(g.Low.tail(M_EXIT).min())
    stop_lvl = peak - ATR_K * a14 if sig else None

    action, order_note = 'بدون تغییر', ''
    need = (target == 0 and cur > 0) or (target > 0 and abs(diff) >= max(1, math.ceil(0.15 * target)))
    if need and diff != 0:
        side = 'buy' if diff > 0 else 'sell'
        action = f"{'خرید' if side == 'buy' else 'فروش'} {abs(diff)} واحد GLD"
        if DRY:
            order_note = ' (آزمایشی خشک: سفارش ثبت نشد)'
        else:
            o = alpaca('POST', '/orders', json={'symbol': 'GLD', 'qty': str(abs(diff)), 'side': side,
                                                'type': 'market', 'time_in_force': 'day'})
            order_note = ' ✅ سفارش ثبت شد' if o.status_code in (200, 201) else f' ❌ خطای سفارش: {o.status_code} {o.text[:120]}'

    day = str(g.index[-1].date())
    lines = [f'<b>🥇 استراتژی روند طلا (حساب آزمایشی)</b> — {day}',
             f"طلا: <b>{g.Close.iloc[-1]:,.1f}</b> | GLD: {px:,.2f}",
             f"سیگنال: <b>{'در بازار ✅' if sig else 'بیرون از بازار ⏸'}</b>",
             f"ورود اگر بسته‌شدن بالای <b>{entry_lvl:,.1f}</b> (سقف ۴۰ روزه)" if not sig else
             f"خروج اگر بسته‌شدن زیر <b>{max(exit_lvl, stop_lvl):,.1f}</b> (کف ۱۰ روزه یا حد ضرر ATR)",
             f"اندازه‌ی موقعیت: {size * 100:.0f}٪ از سهمیه‌ی {SLEEVE:,.0f} دلار (نوسان ۲۰ روزه {vol * 100:.1f}٪)",
             f"GLD در حساب: {cur} واحد → هدف: {target} واحد",
             f"اقدام: <b>{action}</b>{order_note}",
             '<i>اجرای سفارش در بازگشایی بعدی بازار آمریکا. این حساب آزمایشی است.</i>']
    tg('\n'.join(lines))
    os.makedirs('data', exist_ok=True)
    new = not os.path.exists(LOG)
    with open(LOG, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['run_utc', 'data_date', 'gold_close', 'gld_price', 'signal', 'size', 'held', 'target', 'action', 'dry'])
        w.writerow([dt.datetime.utcnow().isoformat(timespec='seconds'), day, round(float(g.Close.iloc[-1]), 2), round(px, 2),
                    sig, round(size, 3), cur, target, action + order_note, DRY])
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
