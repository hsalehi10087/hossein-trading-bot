from engine import performance as P

RULES_OWN = {'a': {'SPY': 1}, 'opt': {'CRGY': 1}}


def owner(sym):
    if sym in ('AAA', 'BBB'):
        return 'a'
    if sym.startswith('CRGY') or sym == 'CRGY':
        return 'opt'
    return None


def fill(sym, side, qty, price, t):
    return {'id': f'{sym}{side}{t}', 'symbol': sym, 'side': side, 'qty': str(qty), 'price': str(price),
            'transaction_time': t}


def test_pl_and_return_from_cashflows():
    fills = [fill('AAA', 'buy', 10, 100, '2026-10-20T14:00:00Z'),
             fill('AAA', 'sell', 4, 120, '2026-10-22T14:00:00Z')]
    pos = [{'symbol': 'AAA', 'market_value': '600'}]   # 6 shares left at 100
    s = P.summarize(fills, pos, owner)['a']
    assert s['start'] == '2026-10-20' and s['trades'] == 2
    assert s['bought'] == 1000 and s['sold'] == 480 and s['value'] == 600
    assert s['pl'] == 80 and abs(s['ret'] - 0.08) < 1e-9


def test_option_multiplier_100():
    fills = [fill('CRGY261120C00035000', 'buy', 15, 4.2, '2026-10-20T14:00:00Z')]
    pos = [{'symbol': 'CRGY261120C00035000', 'market_value': '4800'}]
    s = P.summarize(fills, pos, owner)['opt']
    assert s['bought'] == 6300 and s['pl'] == 4800 - 6300


def test_unowned_bucket_and_no_trades():
    fills = [fill('ZZZ', 'buy', 1, 10, '2026-10-20T14:00:00Z')]
    agg = P.summarize(fills, [], owner)
    assert P.UNOWNED in agg and agg[P.UNOWNED]['bought'] == 10
    assert P.line('x', {'ret': None, 'start': None, 'trades': 0, 'pl': 0, 'bought': 0}, {}).endswith('ثبت نشده')


def test_fetch_fills_paginates():
    pages = [[{'id': str(i), 'symbol': 'A', 'side': 'buy', 'qty': '1', 'price': '1',
               'transaction_time': '2026-10-20T00:00:00Z'} for i in range(100)], [{'id': '100'}], []]
    calls = []

    def get(path, params):
        calls.append(params.get('page_token'))
        return pages.pop(0)
    out = P.fetch_fills(get)
    assert len(out) == 101 and calls[1] == '99'
