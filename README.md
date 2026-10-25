# سبد معاملاتی آزمایشی (Alpaca Paper)

هماهنگ‌کننده‌ی سبد: هر شب (سه‌شنبه تا شنبه، ۱۰ صبح تهران) همه‌ی ربات‌ها را اجرا می‌کند و قبل از هر سفارش از حساب کل اجازه می‌گیرد.

## ربات‌ها
| ربات | فایل | سرمایه | کار |
|---|---|---|---|
| خروج | `exit_agent.py` + `rules/exit_only.yaml` | ۰ | فقط فروش موقعیت‌های قدیمی (توقف متحرک، توقف سخت، اختیار) |
| روند طلا | `gld_trend.py` + `rules/gld_trend.yaml` | ۱۰٬۰۰۰ | خرید GLD وقتی روند طلا صعودی است |
| هسته‌ی QQQ | `qqq_trend.py` + `rules/qqq_trend.yaml` | ۲۰٬۰۰۰ | QQQ در روند صعودی، IEF در غیر آن |

## اجزا
- `run_portfolio.py`: اجرای همه‌ی ربات‌ها و گزارش تلگرام
- `engine/conductor.py`: مالکیت نمادها، سهمیه‌ها، ترمز ریزش، اهرم صفر
- `engine/risk.py`, `engine/core_trend.py`, `engine/decide.py`: منطق تصمیم و چک ریسک
- `engine/broker.py`: ارتباط با Alpaca (فقط آدرس paper پذیرفته می‌شود)
- `close_positions.py` (workflow دستی): بستن دستی موقعیت‌ها با ثبت در `manual/closures.csv`
- `rules/portfolio.yaml`: تقسیم سرمایه و مالک هر نماد؛ هر تغییر با Pull Request و افزایش version

## داده و حسابرسی
نتیجه‌ها در شاخه‌ی `data` ذخیره می‌شوند: `portfolio/daily.csv`، `portfolio/exits.csv`، `qqq/audit.sqlite`، `gld/audit.sqlite`.

## آزمایش
```
pip install pandas numpy requests pyyaml pytest yfinance
python -m pytest -q tests
```

## امنیت
کلیدها فقط در Secrets گیت‌هاب نگه داری می‌شوند. هیچ فایل `config.txt` در ریپو نیست.
