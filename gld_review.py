"""بازبینی پارامترهای استراتژی روند طلا با روش walk-forward — هر ۳ هفته یک بار.

هفته‌ای یک بار اجرا می‌شود ولی فقط اگر ≥۲۱ روز از آخرین بازبینی گذشته باشد کار می‌کند (FORCE=true برای اجرای فوری).
روش: همه‌ی ترکیب‌ها روی ۶ سال آخرِ تا یک سال پیش رتبه‌بندی می‌شوند (آموزش)، سپس روی ۱۲ ماه اخیر که در انتخاب
استفاده نشده (آزمون) سنجیده می‌شوند. پیشنهاد تغییر فقط وقتی داده می‌شود که برنده هم در آموزش (شارپ ≥ ۰.۱ بهتر)
و هم در آزمون (نه بدتر) از پارامترهای فعلی بهتر باشد. هیچ پارامتری خودکار عوض نمی‌شود: پیشنهاد به‌صورت یک
Pull Request روی rules/gld_trend.yaml (با version+1) باز می‌شود و فقط با merge کاربر اعمال می‌شود.
"""
import datetime as dt
import itertools
import json
import math
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from engine import rules as R
from engine.decide import trend_states

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.getenv('DATASTORE', 'datastore'), 'gld', 'param_review.json')
REPO = os.getenv('GITHUB_REPOSITORY', 'hsalehi10087/hossein-trading-bot')
COST, TARGET_VOL = 0.0003, 0.12
FORCE = os.getenv('FORCE', 'false').lower() == 'true'


def flat(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[['High', 'Low', 'Close']].dropna()


def atr(df, n=14):
    tr = pd.concat([df.High - df.Low, (df.High - df.Close.shift()).abs(), (df.Low - df.Close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def positions(df, r, n, m, k):
    p = trend_states(df, n, m, k)[0].state.astype(float)  # همان پیاده‌سازی موتور زنده
    vol = r.rolling(20).std() * math.sqrt(252)
    return p * (TARGET_VOL / vol).clip(upper=1.0).fillna(0)


def metrics(pos, r):
    pos = pos.reindex(r.index).fillna(0)
    s = pos.shift(1).fillna(0) * r - COST * pos.diff().abs().fillna(0)
    if s.std() == 0:
        return {'cagr': 0.0, 'sharpe': 0.0, 'max_dd': 0.0, 'trades': 0}
    eq = (1 + s).cumprod()
    return {'cagr': round((eq.iloc[-1] ** (252 / len(s)) - 1) * 100, 2),
            'sharpe': round(float(s.mean() / s.std() * math.sqrt(252)), 2),
            'max_dd': round(float((eq / eq.cummax() - 1).min() * 100), 2),
            'trades': int(((pos > 0) & (pos.shift(1).fillna(0) == 0)).sum())}


def tg(text):
    tok, chats = os.getenv('TELEGRAM_BOT_TOKEN', ''), [c.strip() for c in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if c.strip()]
    for c in chats:
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                      data={'chat_id': c, 'text': text, 'parse_mode': 'HTML'}, timeout=20)


def open_pr(rules, best, curr, today, fmt, line):
    """شاخه‌ی پیشنهاد می‌سازد، فایل قوانین را با version+1 تغییر می‌دهد و PR باز می‌کند."""
    n, m, k = best['key']
    ver = rules['version'] + 1
    branch = f'gld-review/{today}'
    txt = open(R.RULES_PATH, encoding='utf-8').read()
    txt = re.sub(r'(?m)^version: \d+', f'version: {ver}', txt)
    txt = re.sub(r"(?m)^approved: .*", "approved: 'on-merge'", txt)
    txt = re.sub(r'(?m)^(  n_entry: )\d+', rf'\g<1>{n}', txt)
    txt = re.sub(r'(?m)^(  m_exit: )\d+', rf'\g<1>{m}', txt)
    txt = re.sub(r'(?m)^(  atr_k: )[\d.]+', rf'\g<1>{float(k)}', txt)
    sh = lambda c: subprocess.run(c, shell=True, check=True, cwd=HERE)
    sh(f'git checkout -q -B {branch}')
    open(R.RULES_PATH, 'w', encoding='utf-8').write(txt)
    R.load()  # پیشنهاد هم باید از اعتبارسنجی بگذرد
    sh(f'git add rules/gld_trend.yaml && git commit -q -m "gld rules v{ver}: {n}/{m}/{k:g} (walk-forward review {today})"')
    sh(f'git push -q -f origin {branch}')
    sh('git checkout -q -')
    body = '\n'.join([
        f'بازبینی walk-forward خودکار — {today}', '',
        f'- فعلی (v{rules["version"]}): {fmt(curr["key"])}', f'  - {line("نتیجه", curr)}',
        f'- پیشنهادی (v{ver}): {fmt(best["key"])}', f'  - {line("نتیجه", best)}', '',
        'شرط پیشنهاد: شارپ آموزش دست‌کم ۰.۱ بهتر و شارپ آزمون ۱۲ ماهه نه بدتر.',
        'با merge این PR، اجرای بعدی ربات با قوانین جدید تصمیم می‌گیرد. برای رد، PR را ببندید.'])
    tok = os.getenv('GITHUB_TOKEN', '')
    r = requests.post(f'https://api.github.com/repos/{REPO}/pulls', timeout=20,
                      headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json'},
                      json={'title': f'🥇 قوانین طلا v{ver}: {n}/{m}/{k:g}', 'head': branch, 'base': 'main', 'body': body})
    if r.status_code == 201:
        return r.json()['html_url']
    if r.status_code == 422 and 'already exists' in r.text:
        return f'https://github.com/{REPO}/pulls'
    print('PR create failed', r.status_code, r.text[:300], file=sys.stderr)
    return f'https://github.com/{REPO}/compare/main...{branch}?expand=1'


def main():
    today = dt.date.today()
    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else {}
    last = state.get('last_review')
    if last and not FORCE and (today - dt.date.fromisoformat(last)).days < 21:
        print(f'skip: last review {last}')
        return
    rules = R.load()
    cur = rules['params']
    cur_key = (int(cur['n_entry']), int(cur['m_exit']), float(cur['atr_k']))
    df = flat(yf.download('GC=F', start='2010-01-01', interval='1d', progress=False, auto_adjust=False))
    r = df.Close.pct_change().fillna(0)
    end_train = df.index[-1] - pd.Timedelta(days=365)
    train = r[end_train - pd.Timedelta(days=6 * 365):end_train]
    test = r[end_train + pd.Timedelta(days=1):]
    grid = list(itertools.product([20, 30, 40, 55], [10, 15, 20], [0.0, 2.0, 3.0]))
    if cur_key not in grid:
        grid.append(cur_key)
    rows = []
    for key in grid:
        p = positions(df, r, *key)
        rows.append({'key': key, 'train': metrics(p, train), 'test': metrics(p, test)})
    best = max(rows, key=lambda x: x['train']['sharpe'])
    curr = next(x for x in rows if x['key'] == cur_key)
    better = (best['key'] != cur_key and best['train']['sharpe'] - curr['train']['sharpe'] >= 0.1
              and best['test']['sharpe'] >= curr['test']['sharpe'])

    def fmt(k):
        return f'سقف {k[0]} روزه / کف {k[1]} روزه / حد ضرر {k[2]:g}×ATR' if k[2] else f'سقف {k[0]} روزه / کف {k[1]} روزه / بدون حد ضرر ATR'

    def line(lbl, x):
        return (f"{lbl}: آموزش شارپ {x['train']['sharpe']} (رشد {x['train']['cagr']}٪، افت {x['train']['max_dd']}٪) | "
                f"آزمون ۱۲ ماه شارپ {x['test']['sharpe']} (رشد {x['test']['cagr']}٪، افت {x['test']['max_dd']}٪)")

    msg = [f'<b>🥇 بازبینی ۳ هفته‌ای پارامترهای استراتژی طلا</b> — {today}',
           f'<i>آموزش: {train.index[0].date()} تا {train.index[-1].date()} | آزمون: {test.index[0].date()} تا {test.index[-1].date()}</i>', '',
           f'فعلی: {fmt(cur_key)}', line('  نتیجه', curr), '']
    if better:
        pr = open_pr(rules, best, curr, today, fmt, line)
        msg += [f'پیشنهادی: {fmt(best["key"])}', line('  نتیجه', best), '',
                '<b>✅ پیشنهاد تغییر به‌صورت Pull Request.</b> هیچ تغییری خودکار اعمال نمی‌شود.',
                f'برای اعمال، PR را در GitHub بررسی و merge کنید: {pr}',
                'اگر merge نکنید، قوانین فعلی ادامه پیدا می‌کنند.']
        status = 'pr_opened'
    else:
        msg += ['<b>⏸ پارامترهای فعلی حفظ می‌شوند.</b> ترکیب بهتری که هم در آموزش و هم در آزمون بهتر باشد پیدا نشد.']
        status = 'keep'
    tg('\n'.join(msg))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    state.update({'last_review': today.isoformat(), 'status': status,
                  'current': {'key': list(cur_key), 'train': curr['train'], 'test': curr['test']},
                  'candidate': {'key': list(best['key']), 'train': best['train'], 'test': best['test'],
                                'pr': pr} if better else None})
    state.setdefault('history', []).append({'date': today.isoformat(), 'status': status,
                                            'candidate': list(best['key']) if better else None})
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n'.join(msg))


if __name__ == '__main__':
    main()
