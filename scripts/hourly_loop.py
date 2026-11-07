"""یک اجرای طولانی که تا پایان بازار آمریکا، هر ساعت در دقیقه‌ی مشخص یک فرمان را اجرا می‌کند.

چرا: GitHub اجرای زمان‌بندی‌شده را گاهی چند ساعت دیر شروع می‌کند؛ با این حلقه، هر اجرایی که شروع شود
خودش تا ساعت ۱۶:۱۰ نیویورک ساعت‌به‌ساعت کار می‌کند (حداکثر ۵ ساعت و ۴۵ دقیقه، سقف GitHub ۶ ساعت است).
اجرا: python3 scripts/hourly_loop.py <دقیقه> <فرمان...>
"""
import datetime as dt
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
MAX_SECONDS = 345 * 60


def main():
    minute, cmd = int(sys.argv[1]), sys.argv[2:]
    start = time.time()
    while True:
        now = dt.datetime.now(NY)
        close = now.replace(hour=16, minute=10, second=0, microsecond=0)
        if now.weekday() >= 5 or now >= close:
            print('market day over — loop ends')
            return 0
        nxt = now.replace(minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += dt.timedelta(hours=1)
        while nxt < now.replace(hour=9, minute=35, second=0, microsecond=0):     # اولین نوبت بعد از بازگشایی
            nxt += dt.timedelta(hours=1)
        if nxt > close or time.time() - start + (nxt - now).total_seconds() > MAX_SECONDS:
            print(f'next slot {nxt:%H:%M} NY is past close or the job limit — loop ends')
            return 0
        if time.time() - start > 60:          # اولین بار: فوراً اجرا کن (اگر اجرا دیر شروع شده)
            time.sleep(max(0, (nxt - dt.datetime.now(NY)).total_seconds()))
        print(f'— run at {dt.datetime.now(NY):%H:%M} NY', flush=True)
        subprocess.run(cmd, check=False)


if __name__ == '__main__':
    sys.exit(main())
