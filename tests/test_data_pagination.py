from cryptoauto import data


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_bars_follows_next_page_token(monkeypatch):
    pages = [
        {"bars": {"BTC/USD": [{"c": 1}, {"c": 2}]}, "next_page_token": "tok1"},
        {"bars": {"BTC/USD": [{"c": 3}]}, "next_page_token": None},
    ]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        return _Resp(pages[len(calls) - 1])

    monkeypatch.setattr(data.requests, "get", fake_get)
    bars = data.fetch_bars("BTC/USD", "4Hour")
    assert [b["c"] for b in bars] == [1, 2, 3]
    assert "page_token" not in calls[0]
    assert calls[1]["page_token"] == "tok1"


def test_fetch_bars_caps_pages(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp({"bars": {"BTC/USD": [{"c": 1}]}, "next_page_token": "more"})

    monkeypatch.setattr(data.requests, "get", fake_get)
    bars = data.fetch_bars("BTC/USD", "1Hour", max_pages=3)
    assert len(bars) == 3
