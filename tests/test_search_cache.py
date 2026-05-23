"""Tests for telegram/search_cache.py — SearchCache TTL + capacity."""

import time

from telegram.search_cache import SearchCache


class TestSearchCache:
    def test_set_and_get(self):
        cache = SearchCache(ttl=3600)
        cache.set(1001, ["msg1", "msg2"])
        assert cache.get(1001) == ["msg1", "msg2"]

    def test_get_missing_returns_empty(self):
        cache = SearchCache()
        assert cache.get(999) == []

    def test_get_expired_returns_empty(self, monkeypatch):
        cache = SearchCache(ttl=10)

        fake_time = [1000.0]

        def _time():
            return fake_time[0]

        monkeypatch.setattr(time, "time", _time)

        cache.set(1001, ["msg1"])
        fake_time[0] = 1011.0  # 11 seconds later — past TTL
        assert cache.get(1001) == []

    def test_get_expired_clears_cache(self, monkeypatch):
        cache = SearchCache(ttl=10)
        fake_time = [1000.0]
        monkeypatch.setattr(time, "time", lambda: fake_time[0])

        cache.set(1001, ["data"])
        fake_time[0] = 1011.0
        cache.get(1001)  # triggers expiry
        assert 1001 not in cache.cache

    def test_clear_removes_entry(self):
        cache = SearchCache()
        cache.set(1001, ["data"])
        cache.clear(1001)
        assert cache.get(1001) == []

    def test_clear_missing_does_not_raise(self):
        cache = SearchCache()
        cache.clear(999)  # should not raise

    def test_max_users_evicts_oldest(self, monkeypatch):
        cache = SearchCache(ttl=3600, max_users=3)
        fake_now = [1000.0]
        monkeypatch.setattr(time, "time", lambda: fake_now[0])

        cache.set(1, ["a"])
        fake_now[0] += 1
        cache.set(2, ["b"])
        fake_now[0] += 1
        cache.set(3, ["c"])
        fake_now[0] += 1
        # Now at capacity; adding a 4th should evict user 1
        cache.set(4, ["d"])

        assert cache.get(1) == []  # evicted
        assert cache.get(2) == ["b"]  # still there
        assert cache.get(3) == ["c"]
        assert cache.get(4) == ["d"]

    def test_ttl_not_expired_returns_data(self, monkeypatch):
        cache = SearchCache(ttl=60)
        fake_time = [1000.0]
        monkeypatch.setattr(time, "time", lambda: fake_time[0])

        cache.set(1001, ["still_fresh"])
        fake_time[0] = 1059.0  # 59 seconds later — within TTL
        assert cache.get(1001) == ["still_fresh"]

    def test_set_updates_existing_user(self):
        cache = SearchCache()
        cache.set(1001, ["old"])
        cache.set(1001, ["new"])
        assert cache.get(1001) == ["new"]
