import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import admin_statistics_service
import personalization_service


def test_admin_statistics_builds_each_expired_period_once(monkeypatch):
    admin_statistics_service.clear_statistics_cache()
    calls = []
    snapshot = {"period": "30d", "totals": {}, "users": []}

    def build(_db, period):
        calls.append(period)
        time.sleep(0.05)
        return snapshot

    monkeypatch.setattr(admin_statistics_service, "_build_period_snapshot", build)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: admin_statistics_service.get_user_statistics(object()), range(2)))

    assert calls == ["30d"]
    assert [result["period"] for result in results] == ["30d", "30d"]
    assert [result["users"] for result in results] == [[], []]


def test_personalized_mix_cache_generation_is_single_flight(monkeypatch):
    calls = []
    cache = {}
    payload = {"version": 5, "expires_at": time.time() + 60, "mixes": []}
    user = SimpleNamespace(username="alice")

    monkeypatch.setattr(personalization_service, "_load_mix_cache", lambda username: cache.get(username))

    def generate(_db, _user):
        calls.append(True)
        time.sleep(0.05)
        return payload

    def write(username, value):
        cache[username] = value

    monkeypatch.setattr(personalization_service, "_generate_mixes", generate)
    monkeypatch.setattr(personalization_service, "_write_mix_cache", write)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _index: personalization_service.get_personalized_mixes(object(), user), range(2))
        )

    assert len(calls) == 1
    assert results == [payload, payload]
