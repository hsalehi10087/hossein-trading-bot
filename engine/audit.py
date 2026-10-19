"""دفتر حسابرسی SQLite: هر تصمیم، چک ریسک، سفارش، پرشدن، موقعیت و سود/زیان ثبت می‌شود."""
import csv
import datetime as dt
import json
import os
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS decision(decision_id TEXT PRIMARY KEY, ts_utc TEXT, strategy TEXT, rule_version INT,
  code_commit TEXT, data_date TEXT, symbol TEXT, signal INT, action TEXT, qty INT, dry_run INT, reason_json TEXT,
  outcome TEXT);
CREATE TABLE IF NOT EXISTS risk_check(decision_id TEXT, name TEXT, passed INT, detail TEXT);
CREATE TABLE IF NOT EXISTS "order"(client_order_id TEXT PRIMARY KEY, decision_id TEXT, ts_utc TEXT, symbol TEXT,
  side TEXT, qty INT, status TEXT, broker_order_id TEXT, response TEXT);
CREATE TABLE IF NOT EXISTS fill(client_order_id TEXT PRIMARY KEY, ts_utc TEXT, status TEXT, filled_qty REAL,
  filled_avg_price REAL, filled_at TEXT);
CREATE TABLE IF NOT EXISTS position(ts_utc TEXT, symbol TEXT, qty REAL, avg_entry REAL, market_value REAL,
  unrealized_pl REAL);
CREATE TABLE IF NOT EXISTS pnl_snapshot(ts_utc TEXT, equity REAL, cash REAL, sleeve_unrealized_pl REAL,
  sleeve_realized_pl REAL);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


class Audit:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(DDL)

    def unique_id(self, did):
        """اگر تصمیمی با همین شناسه هست (اجرای دوباره در همان روز)، پسوند rerun می‌گیرد."""
        base, i = did, 1
        while self.db.execute('SELECT 1 FROM decision WHERE decision_id=?', (did,)).fetchone():
            i += 1
            did = f'{base}-rerun{i}'
        return did

    def orders_on(self, data_date, strategy):
        return self.db.execute('SELECT COUNT(*) FROM "order" o JOIN decision d USING(decision_id) '
                               'WHERE d.data_date=? AND d.strategy=? AND o.status NOT LIKE \'rejected%\'',
                               (data_date, strategy)).fetchone()[0]

    def has_order(self, base_id):
        return bool(self.db.execute('SELECT 1 FROM "order" WHERE client_order_id=? AND status NOT LIKE \'rejected%\'',
                                    (base_id,)).fetchone())

    def record_decision(self, d, risk, outcome):
        self.db.execute('INSERT INTO decision VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (d['decision_id'], now(), d['strategy'], d['rule_version'], d['code_commit'], d['data_date'],
                         d['symbol'], d['signal'], d['action'], d['qty'], int(d['dry_run']),
                         json.dumps(d['reason'], ensure_ascii=False), outcome))
        self.db.executemany('INSERT INTO risk_check VALUES(?,?,?,?)',
                            [(d['decision_id'], c['name'], int(c['passed']), c['detail']) for c in risk])
        self.db.commit()

    def record_order(self, coid, did, sym, side, qty, status, broker_id, resp):
        self.db.execute('INSERT OR REPLACE INTO "order" VALUES(?,?,?,?,?,?,?,?,?)',
                        (coid, did, now(), sym, side, qty, status, broker_id, resp[:2000]))
        self.db.commit()

    def open_orders(self):
        return [r[0] for r in self.db.execute(
            'SELECT o.client_order_id FROM "order" o LEFT JOIN fill f USING(client_order_id) '
            'WHERE o.status=\'submitted\' AND (f.status IS NULL OR f.status NOT IN '
            '(\'filled\',\'canceled\',\'expired\',\'rejected\'))')]

    def record_fill(self, coid, status, qty, price, at):
        self.db.execute('INSERT OR REPLACE INTO fill VALUES(?,?,?,?,?,?)', (coid, now(), status, qty, price, at))
        self.db.commit()

    def realized_pl(self, symbol):
        """سود/زیان تحقق‌یافته‌ی سهمیه با روش میانگین هزینه، از روی پرشدن‌های ثبت‌شده."""
        rows = self.db.execute('SELECT o.side, f.filled_qty, f.filled_avg_price FROM fill f JOIN "order" o '
                               'USING(client_order_id) WHERE o.symbol=? AND f.filled_qty>0 ORDER BY f.filled_at',
                               (symbol,)).fetchall()
        qty = cost = pl = 0.0
        for side, q, px in rows:
            if side == 'buy':
                qty, cost = qty + q, cost + q * px
            elif qty > 0:
                avg = cost / qty
                pl += (px - avg) * q
                qty, cost = qty - q, cost - avg * q
        return round(pl, 2)

    def snapshot(self, pos, acct, sleeve_upl, sleeve_rpl):
        if pos:
            self.db.execute('INSERT INTO position VALUES(?,?,?,?,?,?)', (now(), *pos))
        self.db.execute('INSERT INTO pnl_snapshot VALUES(?,?,?,?,?)',
                        (now(), acct.get('equity'), acct.get('cash'), sleeve_upl, sleeve_rpl))
        self.db.commit()

    def export_csv(self, path):
        cur = self.db.execute('SELECT d.decision_id, d.ts_utc, d.rule_version, d.code_commit, d.data_date, d.signal, '
                              'd.action, d.qty, d.dry_run, d.outcome, json_extract(d.reason_json,\'$.rule\'), '
                              'json_extract(d.reason_json,\'$.close\'), json_extract(d.reason_json,\'$.target_qty\') '
                              'FROM decision d ORDER BY d.ts_utc')
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['decision_id', 'ts_utc', 'rule_version', 'code_commit', 'data_date', 'signal', 'action', 'qty',
                        'dry_run', 'outcome', 'rule', 'close', 'target_qty'])
            w.writerows(cur.fetchall())
