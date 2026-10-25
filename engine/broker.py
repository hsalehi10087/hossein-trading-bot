"""رابط بروکر مشترک. فعلاً فقط AlpacaPaper؛ آدرس غیر paper رد می‌شود."""
import requests


class AlpacaPaper:
    def __init__(self, endpoint, key, secret):
        self.base = (endpoint or 'https://paper-api.alpaca.markets/v2').rstrip('/')
        if 'paper-api' not in self.base:
            raise RuntimeError('only Alpaca paper endpoint is allowed at this stage')
        self.h = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}

    def _req(self, method, path, **kw):
        return requests.request(method, self.base + path, headers=self.h, timeout=20, **kw)

    def position(self, symbol):
        r = self._req('GET', f'/positions/{symbol}')
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def positions(self):
        r = self._req('GET', '/positions')
        r.raise_for_status()
        return r.json()

    def account(self):
        r = self._req('GET', '/account')
        r.raise_for_status()
        return r.json()

    def submit(self, symbol, side, qty, client_order_id):
        r = self._req('POST', '/orders', json={'symbol': symbol, 'qty': str(qty), 'side': side, 'type': 'market',
                                               'time_in_force': 'day', 'client_order_id': client_order_id})
        ok = r.status_code in (200, 201)
        return ok, (r.json().get('id') if ok else None), f'{r.status_code} {r.text[:500]}'

    def order_by_client_id(self, coid):
        r = self._req('GET', '/orders:by_client_order_id', params={'client_order_id': coid})
        return r.json() if r.status_code == 200 else None
