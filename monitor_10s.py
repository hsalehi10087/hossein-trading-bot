"""
Read-only 10-second monitor for the Alpaca paper account.
Logs equity, cash and every position (qty, price, unrealized P/L) plus new/changed orders to logs/.
Places NO orders. Runs until the market closes or MAX_MINUTES elapses.
"""
import csv, datetime as dt, os, sys, time
import requests

def cfg(name, default=None):
    v = os.environ.get(name)
    if v:
        return v.strip()
    try:
        for line in open("config.txt", encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default

BASE = cfg("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2").rstrip("/")
H = {"APCA-API-KEY-ID": cfg("ALPACA_KEY") or "", "APCA-API-SECRET-KEY": cfg("ALPACA_SECRET") or ""}
INTERVAL = int(os.environ.get("INTERVAL", "10"))
MAX_MIN = float(os.environ.get("MAX_MINUTES", "350"))

def get(path):
    try:
        r = requests.get(BASE + path, headers=H, timeout=8)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def main():
    os.makedirs("logs", exist_ok=True)
    start = dt.datetime.utcnow()
    path = f"logs/monitor_{start:%Y-%m-%d_%H%M}.csv"
    seen = {}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["utc", "equity", "cash", "symbol", "qty", "price", "unrealized_pl", "event"])
        while (dt.datetime.utcnow() - start).total_seconds() < MAX_MIN * 60:
            t0 = time.time()
            clock = get("/clock")
            if clock is not None and not clock.get("is_open", True):
                if (dt.datetime.utcnow() - start).total_seconds() > 120:   # market closed -> stop
                    break
                time.sleep(INTERVAL)
                continue
            now = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            acct = get("/account") or {}
            pos = get("/positions") or []
            if not acct:
                w.writerow([now, "", "", "", "", "", "", "api_error"])
            for p in pos or [{}]:
                w.writerow([now, acct.get("equity", ""), acct.get("cash", ""), p.get("symbol", ""), p.get("qty", ""),
                            p.get("current_price", ""), p.get("unrealized_pl", ""), ""])
            for o in (get("/orders?status=all&limit=20&direction=desc") or []):
                key = (o["id"], o["status"], o.get("filled_qty"))
                if seen.get(o["id"]) != key:
                    seen[o["id"]] = key
                    w.writerow([now, "", "", o["symbol"], o.get("filled_qty") or o.get("qty"), o.get("filled_avg_price") or "",
                                "", f"order {o['side']} {o['status']}"])
            f.flush()
            time.sleep(max(0, INTERVAL - (time.time() - t0)))
    print(f"log written: {path}")

if __name__ == "__main__":
    main()
