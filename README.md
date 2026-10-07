# 📈 1-Hour Intraday Trading Strategy

استراتژی معاملاتی خودکار یک ساعتی با **GitHub Actions** (بدون سرور)

```
📊 +7.33% در 24 روز | 🎯 ~9.3% ماهانه | 🚀 خودکار 100%
```

---

## 🎯 معنی‌ای:

| سیستم | رژیم | ورود | خروج | هدف |
|-------|------|------|------|------|
| **1H Intraday** | Bull/Bear/Range | 3-level pyramid | TS 1% / TP 2.5% | +9% ماهانه |

---

## ⚡ شروع فوری (GitHub Actions):

### ۱. Repository ایجاد:
```bash
# github.com/new
# Name: stock-scanner
# Private: ✅
```

### ۲. Clone + Push:
```bash
git clone YOUR_REPO
cd stock-scanner

# تمام فایل‌ها را کپی کن
cp strategy_1h*.py .
cp .github/workflows/strategy_1h.yml .github/workflows/

git add .
git commit -m "Initial commit: 1H strategy"
git push
```

### ۳. Secrets تنظیم:
```
GitHub Settings → Secrets and variables → Actions
```

اضافه کن (6 secret):
```
ALPACA_ENDPOINT
ALPACA_KEY
ALPACA_SECRET
FINNHUB_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

### ۴. شروع:
```
GitHub → Actions tab → Enable workflows
```

---

## 📦 فایل‌ها:

```
📁 stock-scanner/
├── 📄 strategy_1h.py              شبیه‌سازی (+7.33%)
├── 📄 strategy_1h_rest.py         **← اصلی (Alpaca REST)**
├── 📄 strategy_1h_dryrun.py       تست dry-run
├── 🔧 .github/workflows/          
│   └── strategy_1h.yml            **← GitHub Actions**
├── 📋 GITHUB_ACTIONS_SETUP.md     راهنمای Actions
├── 📋 DEPLOYMENT_GUIDE.md         راهنمای سرور
├── 📋 README_1H_STRATEGY.md       تکنیکال
├── 🔐 config.txt                  API keys (PRIVATE!)
├── ⚙️ schedule_1h.sh              cron script
└── 📝 .gitignore
```

---

## 🔄 چگونه کار می‌کند:

```
GitHub Actions Scheduler (UTC)
        ↓
سی ام 30 14-21 * * 1-5
(هر ساعت، ۹:۳۰ AM - ۴:۰۰ PM ET)
        ↓
Python: strategy_1h_rest.py
        ↓
┌─────────────────┬──────────────┬─────────────────┐
│ Finnhub API     │ Alpaca API   │ Telegram Bot    │
│ (قیمت)         │ (سفارش)      │ (اطلاع)         │
└─────────────────┴──────────────┴─────────────────┘
        ↓
State file: strategy_1h_live_state.json
        ↓
Save to GitHub repo
```

---

## 📊 نتایج:

### شبیه‌سازی (24 روز):
```
Symbol    Return $    Return %    Status
NVDA      +$673       +6.96%      ✅
PLTR      +$468       +4.58%      ✅
AAPL      +$849       +9.30%      ✅
MSFT      +$563       +6.19%      ✅
...
TOTAL     +$7,331     +7.33%      ✅ (11/11)
```

### پیش‌بینی:
```
24 days   = +7.33%
1 month   = +9.3%
1 year    = +112%
```

---

## 🎯 استراتژی:

### Regime Detection:
```
BULL:  شیب > +1% → ورود
BEAR:  شیب < -1% → تعطیل
RANGE: وسط → تعطیل
```

### Entry (3-Level Pyramid):
```
Level 1:  20% of allocation → BUY_L1
  ↓ (if -0.8% dip)
Level 2:  +30% more → BUY_L2
  ↓ (if -1.5% dip)
Level 3:  +50% more → BUY_L3
```

### Exit:
```
Trailing Stop:  -1% loss  → EXIT
Take Profit:    +2.5% gain → EXIT
```

---

## 📲 Telegram Alerts:

هر ساعت:
```
🟢 1H Strategy Signals

✅ BUY_L1 NVDA: 50 @ $138.25
✅ BUY_L2 PLTR: 30 @ $32.00
🛑 EXIT_STOP AAPL: 32 @ $226.50 | PnL: -0.83%
✅ EXIT_TP META: 25 @ $355.20 | PnL: +2.65%

✅ Cycle completed: 4 signals
```

---

## ⚙️ تنظیمات:

```python
# strategy_1h_rest.py

REGIME_WINDOW = 10          # شمع‌های بررسی
ENTRY_L1 = 0.20            # 20% در Level 1
ENTRY_L2 = 0.30            # +30% در Level 2
ENTRY_L3 = 0.50            # +50% در Level 3
DIPS = [0.992, 0.985]      # تحریک: 0.8%, 1.5%
STOP = 0.99                # -1% loss
TP = 1.025                 # +2.5% gain
```

---

## 🔐 امنیت:

- ✅ **Private repository** (حتماً!)
- ✅ Secrets در GitHub (نه GitHub میں hardcode)
- ✅ .gitignore: config.txt
- ✅ Paper account فقط (اول)

---

## 🛠️ تریبل‌شوت:

### ❌ Workflow نمی‌دوید:
```
Settings → Actions → General → Enable workflows ✅
```

### ❌ Secrets error:
```
آن دوبارہ؟ آن 6 secrets اضافہ کن:
ALPACA_ENDPOINT
ALPACA_KEY
ALPACA_SECRET
FINNHUB_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

### ❌ Alpaca API failure:
```
Check: API key موجود و درست
Check: Paper account activated
Check: Endpoint = https://paper-api.alpaca.markets/v2
```

---

## 📞 لینک‌های مفید:

- [Alpaca API Docs](https://alpaca.markets/docs)
- [Finnhub API Docs](https://finnhub.io/docs)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [GitHub Actions](https://docs.github.com/en/actions)

---

## 🚀 بعدی:

```
1. Repository ایجاد
2. فایل‌ها push
3. Secrets تنظیم
4. GitHub Actions enable
5. Telegram alerts بررسی
6. Alpaca dashboard مانیتور
```

---

## 📊 مانیتورینگ:

### GitHub Actions Logs:
```
Actions tab → 1-Hour Intraday Strategy → Latest run
```

### State File:
```
strategy_1h_live_state.json
(positions, entry prices, levels)
```

### Alpaca Dashboard:
```
https://app.alpaca.markets
Paper Account → Positions → PnL
```

---

## 💡 نکات:

- **هیچ سرور نیازی نیست** ✅
- **رایگان** ✅
- **خودکار** ✅
- **24/7 اجرا** ✅ (ساعات بازار)
- **اطلاع فوری** ✅ (Telegram)

---

## 📈 نتیجه‌گیری:

```
مثل یک معامله‌گر حرفه‌ای کار می‌کند:
✓ هر ساعت بازار را بررسی کند
✓ اتو سفارش دهد
✓ اتو بند دهد
✓ اطلاع بدهد
✓ وضعیت ذخیره کند

بدون سرور، بدون خرچ، بدون دردسر.
```

---

**آماده برای شروع؟** 🚀

**سؤالی؟** مراجعه کن:
- `GITHUB_ACTIONS_SETUP.md`
- `README_1H_STRATEGY.md`
- `DEPLOYMENT_GUIDE.md`
