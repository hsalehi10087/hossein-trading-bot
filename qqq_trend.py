"""اجراکننده‌ی روزانه‌ی استراتژی «هسته با فیلتر روند» (QQQ ↔ IEF) روی Alpaca paper.

جریان: داده → engine.core_trend.decide (قوانین rules/qqq_trend.yaml) → چک ریسک برای هر سفارش →
سفارش با client_order_id = decision_id-<نماد> → دفتر حسابرسی SQLite روی شاخه‌ی data → پیام فارسی با دلیل.
DRY_RUN=true → سفارشی ثبت نمی‌شود. KILL_SWITCH=true → خرید مسدود.
"""
import datetime as dt
import os
import sys

import pandas as pd
import requests
import yfinance as yf

from engine import core_trend as C
from engine.audit import Audit
from engine.broker import AlpacaPaper
from engine import data as D

ROOT = os.path.dirname(os.path.abspath(__file__))
DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
KILL = os.getenv('KILL_SWITCH', 'false').lower() == 'true'
STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
COMMIT = (os.getenv('GITHUB_SHA') or 'local')[:7]
RULE_FA = {'enter_risk': 'ورود به QQQ — قیمت از میانگین ۲۰۰ روزه + ۲٪ بالاتر رفت',
           'exit_to_safe': 'خروج به اوراق IEF — قیمت از میانگین ۲۰۰ روزه − ۲٪ پایین‌تر رفت',
           'hold_risk': 'ماندن در QQQ — روند صعودی برقرار است',
           'hold_safe': 'ماندن در اوراق IEF — روند هنوز صعودی نشده'}
CHECK_FA = {'symbol_allowed': 'نماد مجاز', 'paper_only': 'فقط حساب آزمایشی', 'kill_switch': 'کلید توقف',
            'max_order_usd': 'سقف ارزش سفارش', 'max_position_usd': 'سقف ارزش موقعیت',
            'max_orders_per_day': 'سقف سفارش روزانه', 'not_duplicate': 'سفارش تکراری نباشد',
            'data_fresh': 'به‌روز بودن داده'}


def last_close(sym, **kw):
    df = yf.download(sym, interval='1d', progress=False, auto_adjust=False, **kw)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df['Close'].dropna()


def tg(text):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        try:
            requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'}, timeout=20)
        except Exception as e:
            print('telegram error', e, file=sys.stderr)


def main(conductor=None, session=None, notify=True):
    rules = C.load(os.path.join(ROOT, 'rules', 'qqq_trend.yaml'))
    ra, sa = rules['risk_asset'], rules['safe_asset']
    audit = Audit(os.path.join(STORE, 'qqq', 'audit.sqlite'))
    broker = AlpacaPaper(ENDPOINT, os.getenv('ALPACA_KEY', ''), os.getenv('ALPACA_SECRET', ''))

    # پرشدن سفارش‌های قبلی
    for coid in audit.open_orders():
        o = broker.order_by_client_id(coid)
        if o:
            audit.record_fill(coid, o['status'], float(o.get('filled_qty') or 0),
                              float(o['filled_avg_price']) if o.get('filled_avg_price') else None, o.get('filled_at'))

    try:  # داده‌ی رسمی Alpaca (تعدیل‌شده)؛ اگر نشد Yahoo
        px_df = D.daily_closes([ra, sa], start='2005-01-01')
        close, safe_px, src = px_df[ra].dropna(), float(px_df[sa].dropna().iloc[-1]), 'Alpaca'
    except Exception as e:
        print('alpaca data failed, using Yahoo:', e, file=sys.stderr)
        close, safe_px, src = last_close(ra, start='2005-01-01'), float(last_close(sa, period='1mo').iloc[-1]), 'Yahoo'
    if session and close.index[-1].date() < session:
        msg = f'📈 هسته‌ی QQQ: داده برای {session} هنوز نرسیده (آخرین: {close.index[-1].date()})؛ امروز تصمیمی گرفته نشد.'
        print(msg)
        if notify:
            tg(msg)
        return {'sleeve': 'qqq_trend', 'stale': True, 'lines': [msg]}
    prices = {ra: float(close.iloc[-1]), sa: safe_px}
    held = {}
    for s in (ra, sa):
        p = broker.position(s)
        held[s] = int(float(p['qty'])) if p else 0

    d = C.decide(rules, close, prices, held, COMMIT, DRY)
    base = d['decision_id']
    d['decision_id'] = audit.unique_id(base)
    today = dt.date.today()
    risk_all, notes, outcomes = [], [], []
    n_today = audit.orders_on(d['data_date'], d['strategy'])
    for o in d['orders']:
        coid = f'{base}-{o["symbol"]}'
        dup = audit.has_order(coid)
        rc = C.checks(rules, d, o, today, n_today, dup, KILL, ENDPOINT)
        risk_all += [dict(c, name=f'{o["symbol"]}:{c["name"]}') for c in rc]
        bad = [c for c in rc if not c['passed']]
        label = f"{'خرید' if o['side'] == 'buy' else 'فروش'} {o['qty']} واحد {o['symbol']}"
        if not bad and conductor is not None:
            q, pc = conductor.approve('qqq_trend', o)
            risk_all += [dict(c, name=f'{o["symbol"]}:{c["name"]}') for c in pc]
            if q == 0:
                bad = [c for c in pc if not c['passed']]
            elif q < o['qty']:
                o['qty'] = q
                label += f' (کوچک‌شده به {q})'
        if bad:
            outcomes.append('blocked')
            notes.append(f'{label} ⛔ مسدود: ' + '، '.join(CHECK_FA.get(c['name'], c['name']) for c in bad))
        elif DRY:
            outcomes.append('dry_run')
            notes.append(f'{label} (آزمایشی خشک: ثبت نشد)')
        else:
            ok, bid, resp = broker.submit(o['symbol'], o['side'], o['qty'], coid)
            audit.record_order(coid, d['decision_id'], o['symbol'], o['side'], o['qty'],
                               'submitted' if ok else f'rejected {resp[:60]}', bid, resp)
            n_today += 1 if ok else 0
            outcomes.append('submitted' if ok else 'rejected')
            notes.append(f'{label} ' + ('✅ سفارش ثبت شد' if ok else f'❌ رد شد: {resp[:80]}'))
    audit.record_decision(d, risk_all, ','.join(outcomes) or 'no_action')

    acct = broker.account()
    upl = 0.0
    for s in (ra, sa):
        p = broker.position(s)
        if p:
            upl += float(p['unrealized_pl'])
            audit.snapshot((s, float(p['qty']), float(p['avg_entry_price']), float(p['market_value']),
                            float(p['unrealized_pl'])), acct, None, None)
    rpl = audit.realized_pl(ra) + audit.realized_pl(sa)
    audit.snapshot(None, acct, upl, rpl)
    audit.export_csv(os.path.join(STORE, 'qqq', 'decisions.csv'))

    x = d['reason']
    lines = [f'<b>📈 هسته‌ی روند QQQ — حساب آزمایشی</b> — {d["data_date"]}',
             f"QQQ: <b>{x['close']:,.2f}</b> | میانگین ۲۰۰ روزه: {x['sma']:,.2f} ({x['dist_pct']:+.1f}٪)",
             f"وضعیت: <b>{'در QQQ 🟢' if d['signal'] else 'در اوراق IEF 🛡'}</b>",
             f"دلیل: {RULE_FA[x['rule']]}",
             (f"خروج به اوراق اگر QQQ زیر <b>{x['exit_below']:,.2f}</b> بسته شود" if d['signal'] else
              f"ورود به QQQ اگر بالای <b>{x['enter_above']:,.2f}</b> بسته شود"),
             f"هدف: {x['target'][ra]} واحد {ra} و {x['target'][sa]} واحد {sa} | در حساب: {held[ra]} و {held[sa]}",
             'اقدام: ' + (' | '.join(notes) if notes else '<b>بدون تغییر</b>'),
             f'سود/زیان سهمیه: باز {upl:+,.0f}$ | بسته‌شده {rpl:+,.0f}$',
             f"<i>قانون نسخه‌ی {d['rule_version']} | داده: {src} | کد {COMMIT} | شناسه {d['decision_id']}</i>"]
    msg = '\n'.join(lines)
    print(msg)
    if notify:
        tg(msg)
    val = sum(float(p['market_value']) for p in (broker.position(ra), broker.position(sa)) if p)
    return {'sleeve': 'qqq_trend', 'stale': False, 'lines': lines, 'signal': d['signal'],
            'action': ' | '.join(notes) if notes else 'بدون تغییر', 'upl': upl, 'rpl': rpl, 'value': val}


if __name__ == '__main__':
    main()
