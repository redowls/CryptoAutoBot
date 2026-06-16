import pytest
from cryptoauto import data


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_bars_returns_symbol_list(monkeypatch):
    payload = {"bars": {"BTC/USD": [{"c": 1, "h": 2, "l": 0.5, "o": 1, "v": 10, "t": "x"}]}}
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _FakeResp(payload))
    bars = data.fetch_bars("BTC/USD", "1Hour", limit=5)
    assert isinstance(bars, list) and bars[0]["c"] == 1


def test_fetch_bars_missing_symbol_returns_empty(monkeypatch):
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _FakeResp({"bars": {}}))
    assert data.fetch_bars("UNI/USD", "1Hour") == []


def test_fetch_bars_http_error_raises_fetcherror(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("dns fail")
    monkeypatch.setattr(data.requests, "get", boom)
    with pytest.raises(data.FetchError):
        data.fetch_bars("BTC/USD", "1Hour")
