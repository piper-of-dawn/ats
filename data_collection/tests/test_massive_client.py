import io
import json
from urllib.error import HTTPError

import pytest

from data_collection import massive


@pytest.fixture
def client(monkeypatch):
    clock = {"now": 0.0}
    replies, requests = [], []
    def sleep(seconds):
        clock["now"] += seconds
    def open_request(request, timeout):
        requests.append((clock["now"], request))
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())
    monkeypatch.setenv("MASSIVE_API_KEY", "canonical-secret")
    monkeypatch.setenv("POLYGON_IO", "legacy-secret")
    monkeypatch.delenv("MASSIVE_API_BASE_URL", raising=False)
    monkeypatch.delenv("POLYGON_API_BASE_URL", raising=False)
    monkeypatch.setattr(massive, "_limiter", massive._RequestLimiter(clock=lambda: clock["now"], sleep=sleep))
    monkeypatch.setattr(massive, "_open", open_request)
    return replies, requests


def test_keys_are_runtime_parameters_and_never_in_urls(client, monkeypatch):
    replies, requests = client
    replies.extend([{}] * 3)
    massive.request_json("/reference")
    massive.request_json("/reference", api_key="explicit-secret")
    monkeypatch.delenv("MASSIVE_API_KEY")
    massive.request_json("/reference")
    assert [r.get_header("Authorization") for _, r in requests] == [
        "Bearer canonical-secret", "Bearer explicit-secret", "Bearer legacy-secret",
    ]
    assert all("secret" not in request.full_url for _, request in requests)
    assert [t for t, _ in requests] == [0, 13, 26]


@pytest.mark.parametrize("destination", ["https://evil.example/next", "https://api.massive.com:8443/next", "https://user@api.massive.com/next"])
def test_pagination_cannot_forward_credentials_off_origin(client, destination):
    client[0].append({"results": [], "next_url": destination})
    with pytest.raises(massive.MassiveAPIError, match="origin"):
        massive.fetch_records("/reference")
    assert len(client[1]) == 1


def test_429_retries_with_cooldown_and_redacted_errors(client):
    client[0].extend([
        HTTPError("https://api.massive.com/?apiKey=canonical-secret", 429, "canonical-secret", {"Retry-After": "90"}, io.BytesIO()),
        {"results": [{"ticker": "AAPL"}]},
    ])
    assert massive.fetch_records("/reference") == [{"ticker": "AAPL"}]
    assert [t for t, _ in client[1]] == [0, 90]


def test_503_retry_budget_is_bounded(client):
    client[0].extend(HTTPError("https://api.massive.com/", 503, "canonical-secret", {}, io.BytesIO()) for _ in range(3))
    with pytest.raises(massive.MassiveAPIError, match="exhausted three attempts") as exc:
        massive.request_json("/reference")
    assert "secret" not in str(exc.value)
    assert [t for t, _ in client[1]] == [0, 60, 120]


def test_runtime_base_url_can_be_changed(client, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_BASE_URL", "https://api.polygon.io")
    client[0].append({})
    massive.request_json("/reference")
    assert client[1][0][1].full_url == "https://api.polygon.io/reference"
