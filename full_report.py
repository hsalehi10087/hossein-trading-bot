"""گزارش کل حساب — فقط خواندن، بدون هیچ سفارشی."""
import os, requests, datetime as dt
from engine import conductor as PC

ENDPOINT = (os.getenv('ALPACA_ENDPOINT') or 'https://paper-api.alpaca.markets/v2').rstrip('/')
if not ENDPOINT.endswith('/v2'): ENDPOINT += '/v2'
H = {'APCA-API-KEY-ID': os.getenv('ALPACA_KEY',''), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET','')}
def get(p): 
    r=requests.get(ENDPOINT+p, headers=H, timeout=30); r.raise_for_status(); return r.json()

rules = PC.load()
acct, positions = get('/account'), get('/positions')
cond = PC.Conductor(rules, acct, positions, 'datastore/portfolio/state.json')
orphans = cond.orphans()
by_sleeve = {}
for p in positions:
    s = PC.owner_of(rules, p['symbol']) or 'بی‌صاحب'
    v = by_sleeve.setdefault(s, {'value':0.0,'upl':0.0,'n':0})
    v['value'] += float(p['market_value']); v['upl'] += float(p['unrealized_pl']); v['n'] += 1

eq, cash = float(acct['equity']), float(acct['cash'])
NAMES = {'gld_trend':'🥇 روند طلا','qqq_trend':'📈 هسته‌ی QQQ','legacy_1h':'⏱ ربات ساعتی','copy_trade':'👥 کپی ترید',
         'btc_momentum':'₿ مومنتوم بیت‌کوین','eth_weekend':'⟠ اتریوم آخر هفته','dbmf_band':'📐 DBMF روند آتی‌ها',
         'spy_10m':'📐 میانگین ۱۰ ماهه','qqq_200':'📐 QQQ میانگین ۲۰۰','growth_signal':'🌱 سیگنال رشد'}
print(f"💼 کل حساب (آزمایشی) — {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print(f"ارزش حساب: {eq:,.0f}$ | نقد: {cash:,.0f}$ | فاصله از سقف: {cond.drawdown*100:+.1f}%" + (' | ترمز ریزش فعال' if cond.brake else ''))
print()
tot_val = tot_upl = 0
for name, s in rules['sleeves'].items():
    v = by_sleeve.get(name, {'value':0.0,'upl':0.0,'n':0})
    tot_val += v['value']; tot_upl += v['upl']
    print(f"{NAMES.get(name,name):20} سرمایه {s['capital_usd']:>7,}$ | در بازار {v['value']:>9,.0f}$ | سود/زیان باز {v['upl']:>+8,.0f}$ | {v['n']} پوزیشن")
print()
print(f"جمع در بازار: {tot_val:,.0f}$ | جمع سود/زیان باز: {tot_upl:+,.0f}$")
if orphans:
    print("\n⚠️ بی‌صاحب:")
    for s,q,v in orphans: print(f"  {s}: {q:g} واحد، {v:,.0f}$")
