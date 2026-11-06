"""Retry accounting uses fake HTTP errors only; no Google calls or sleeps."""
from types import SimpleNamespace

from googleapiclient.errors import HttpError
import pytest

from scraper import google_client


def http_error(status, reason="rateLimitExceeded"):
    return HttpError(SimpleNamespace(status=status, reason=reason), b'{}')


@pytest.mark.parametrize("status,count", [(429, 2), (403, 2), (503, 0)])
def test_execute_counts_only_rate_limit_retries(monkeypatch, status, count):
    attempts, retries, sleeps = [], [], []
    def execute():
        attempts.append(True)
        if len(attempts) < 3:
            raise http_error(status)
        return {"ok": True}
    monkeypatch.setattr(google_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(google_client.random, "random", lambda: 0)
    assert google_client.execute(SimpleNamespace(execute=execute), on_rate_limit=lambda: retries.append(True)) == {"ok": True}
    assert len(retries) == count and sleeps == [1, 2]


def test_exhausted_and_nonretryable_errors_do_not_count_a_retry_that_never_happens(monkeypatch):
    retries = []
    monkeypatch.setattr(google_client.time, "sleep", lambda _: None)
    def fail():
        raise http_error(429)
    with pytest.raises(HttpError):
        google_client.execute(SimpleNamespace(execute=fail), tries=2, on_rate_limit=lambda: retries.append(True))
    assert retries == [True]
    retries.clear()
    def forbidden():
        raise http_error(403, "forbidden")
    with pytest.raises(HttpError):
        google_client.execute(SimpleNamespace(execute=forbidden), on_rate_limit=lambda: retries.append(True))
    assert retries == []
