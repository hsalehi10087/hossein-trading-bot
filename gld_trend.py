"""اجراکننده‌ی روزانه‌ی استراتژی روند طلا روی GLD (Alpaca paper).

جریان: داده → engine.decide (قوانین rules/gld_trend.yaml) → engine.risk → سفارش با client_order_id=decision_id
→ ثبت همه‌چیز در دفتر حسابرسی SQLite (شاخه‌ی data) → گزارش فارسی با دلیل تصمیم به تلگرام.
هیچ دستور لحظه‌ای انسانی وارد این مسیر نمی‌شود؛ تنها راه تغییر رفتار، تغییر فایل قوانین با Pull Request است.
DRY_RUN=true → سفارش ثبت نمی‌شود. KILL_SWITCH=true (متغیر مخزن) → خرید جدید مسدود.
"""
import datetime as dt
import os
import sys

import pandas as pd
import requests
import yfinance as yf

from engine import rules as R
from engine.audit import Audit
from engine.broker import AlpacaPaper
from engine.decide import decide
from engine.risk import checks

DRY = os.getenv('DRY_RUN', 'false').lower() == 'true'
KILL = os.getenv('KILL_SWITCH', 'false').lower() == 'true'
STORE = os.getenv('DATASTORE', 'datastore')
ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
COMMIT = (os.getenv('GITHUB_SHA') or 'local')[:7]

RULE_FA = {'entry_breakout': 'ورود — بسته‌شدن بالای سقف {n} روز قبل',
           'exit_channel': 'خروج — بسته‌شدن زیر کف {m} روز قبل',
           'exit_atr_stop': 'خروج — برخورد به حد ضرر شناور {k:g}×ATR',
           'hold_in': 'ماندن در موقعیت — هیچ شرط خروجی برقرار نشد',
           'hold_out': 'بیرون ماندن — شکست سقف {n} روزه رخ نداد'}
CHECK_FA = {'symbol_allowed': 'نماد مجاز', 'paper_only': 'فقط حساب آزمایشی', 'kill_switch': 'کلید توقف',
            'max_order_usd': 'سقف ارزش سفارش', 'max_position_usd': 'سقف ارزش موقعیت',
            'max_orders_per_day': 'سقف سفارش روزانه', 'not_duplicate': 'سفارش تکراری نباشد',
            'data_fresh': 'به‌روز بودن داده'}


def flat(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[['High', 'Low', 'Close']].dropna()


def tg(text):
    tok = os.getenv('TELEGRAM_BOT_TOKEN', '')
    for c in [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip() and c.strip() != 'none']:
        try:
            requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'}, timeout=20)
        except Exception as e:
            print('telegram error', e, file=sys.stderr)


def reconcile(audit, broker):
    """وضعیت سفارش‌های باز قبلی را از بروکر می‌خواند و پرشدن‌ها را ثبت می‌کند."""
    notes = []
    for coid in audit.open_orders():
        o = broker.order_by_client_id(coid)
        if not o:
            continue
        q = float(o.get('filled_qty') or 0)
        px = float(o['filled_avg_price']) if o.get('filled_avg_price') else None
        audit.record_fill(coid, o['status'], q, px, o.get('filled_at'))
        if o['status'] == 'filled':
            notes.append(f"{'خرید' if o['side'] == 'buy' else 'فروش'} {q:g} واحد به قیمت {px:,.2f} پر شد")
        elif o['status'] in ('canceled', 'expired', 'rejected'):
            notes.append(f'سفارش {coid} وضعیت {o["status"]} گرفت')
    return notes


def main():
    rules = R.load()
    sym = rules['trade_symbol']
    audit = Audit(os.path.join(STORE, 'gld', 'audit.sqlite'))
    broker = AlpacaPaper(ENDPOINT, os.getenv('ALPACA_KEY', ''), os.getenv('ALPACA_SECRET', ''))
    fills = reconcile(audit, broker)

    gold = flat(yf.download(rules['signal_symbol'], start='2008-01-01', interval='1d', progress=False,
                            auto_adjust=False))
    px = float(flat(yf.download(sym, period='1mo', interval='1d', progress=False, auto_adjust=False)).Close.iloc[-1])
    pos = broker.position(sym)
    held = int(float(pos['qty'])) if pos else 0

    d = decide(rules, gold, px, held, COMMIT, DRY)
    base_id = d['decision_id']
    d['decision_id'] = audit.unique_id(base_id)
    risk = checks(rules, d, dt.date.today(), audit.orders_on(d['data_date'], d['strategy']),
                  audit.has_order(base_id), KILL, ENDPOINT)
    failed = [c for c in risk if not c['passed']]

    if d['action'] == 'none':
        outcome, note = 'no_action', ''
    elif failed:
        outcome = 'blocked:' + ','.join(c['name'] for c in failed)
        note = ' ⛔ مسدود شد: ' + '، '.join(CHECK_FA.get(c['name'], c['name']) for c in failed)
    elif DRY:
        outcome, note = 'dry_run', ' (آزمایشی خشک: سفارش ثبت نشد)'
    else:
        ok, bid, resp = broker.submit(sym, d['action'], d['qty'], base_id)
        outcome = 'submitted' if ok else 'rejected'
        audit.record_order(base_id, d['decision_id'], sym, d['action'], d['qty'],
                           'submitted' if ok else f'rejected {resp[:60]}', bid, resp)
        note = ' ✅ سفارش ثبت شد' if ok else f' ❌ بروکر رد کرد: {resp[:100]}'
    audit.record_decision(d, risk, outcome)

    acct = broker.account()
    pos = broker.position(sym)
    upl = float(pos['unrealized_pl']) if pos else 0.0
    rpl = audit.realized_pl(sym)
    audit.snapshot((sym, float(pos['qty']), float(pos['avg_entry_price']), float(pos['market_value']), upl)
                   if pos else None, acct, upl, rpl)
    audit.export_csv(os.path.join(STORE, 'gld', 'decisions.csv'))

    x, p = d['reason'], rules['params']
    act = {'buy': f"خرید {d['qty']} واحد {sym}", 'sell': f"فروش {d['qty']} واحد {sym}"}.get(d['action'], 'بدون تغییر')
    level = (f"ورود فردا اگر بسته‌شدن بالای <b>{x['next_entry_level']:,.1f}</b>" if not d['signal'] else
             f"خروج فردا اگر بسته‌شدن زیر <b>{max(x['next_exit_level'], x['stop_level_today'] or 0):,.1f}</b>")
    lines = [f'<b>🥇 روند طلا — حساب آزمایشی</b> — {d["data_date"]}',
             f"طلا: <b>{x['close']:,.1f}</b> | {sym}: {x['gld_price']:,.2f}",
             f"وضعیت: <b>{'در بازار ✅' if d['signal'] else 'بیرون از بازار ⏸'}</b>",
             f"دلیل: {RULE_FA[x['rule']].format(n=p['n_entry'], m=p['m_exit'], k=float(p['atr_k']))}",
             level,
             f"اندازه: {x['size_frac'] * 100:.0f}٪ سهمیه (نوسان سالانه {x['vol_annual'] * 100:.1f}٪) → هدف "
             f"{x['target_qty']} واحد، در حساب {x['held_qty']}",
             f'اقدام: <b>{act}</b>{note}']
    if fills:
        lines.append('پرشدن‌ها: ' + ' | '.join(fills))
    lines += [f"سود/زیان سهمیه: باز {upl:+,.0f}$ | بسته‌شده {rpl:+,.0f}$",
              f"<i>قانون نسخه‌ی {d['rule_version']} | کد {COMMIT} | شناسه {d['decision_id']}</i>"]
    msg = '\n'.join(lines)
    print(msg)
    tg(msg)


if __name__ == '__main__':
    main()
