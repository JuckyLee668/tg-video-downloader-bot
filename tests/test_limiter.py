"""Tests for telegram/limiter.py — MessageRateLimiter."""

import asyncio

from telegram.limiter import MessageRateLimiter


class TestMessageRateLimiter:
    """Rate limiter tests using fast-forwardable mock time."""

    async def test_allows_below_limit(self):
        limiter = MessageRateLimiter(max_per_second=5, max_per_minute=15)
        for _ in range(5):
            await limiter.wait()  # should not block
        # If we got here without hanging, it passed

    async def test_enforces_per_second_limit(self, monkeypatch):
        limiter = MessageRateLimiter(max_per_second=2, max_per_minute=60)
        fake_now = [1000.0]

        monkeypatch.setattr("time.time", lambda: fake_now[0])

        # Consume the 2 per-second slots
        await limiter.wait()
        await limiter.wait()

        slept_for = [0.0]

        async def _fake_sleep(duration):
            slept_for[0] = duration
            fake_now[0] += duration

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

        await limiter.wait()
        # Should have been told to sleep for some positive duration
        assert slept_for[0] > 0

    async def test_enforces_per_minute_limit(self, monkeypatch):
        limiter = MessageRateLimiter(max_per_second=100, max_per_minute=3)

        real_now = [1000.0]

        def _time():
            return real_now[0]

        monkeypatch.setattr("time.time", _time)

        slept_for = [0.0]

        async def _fake_sleep(duration):
            slept_for[0] = duration
            real_now[0] += duration

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

        # Consume the 3 per-minute slots
        for _ in range(3):
            await limiter.wait()

        # 4th call — now per-minute limit hit
        await limiter.wait()
        # Should sleep until oldest entry (real_now[0] - 60) expires
        assert slept_for[0] > 0

    async def test_concurrent_safety_with_lock(self):
        """Multiple concurrent wait() calls should not corrupt state."""
        limiter = MessageRateLimiter(max_per_second=10, max_per_minute=30)

        async def _hammer():
            for _ in range(5):
                await limiter.wait()

        await asyncio.gather(*[_hammer() for _ in range(5)])
        assert len(limiter.global_history) == 25

    async def test_invalidates_old_history(self, monkeypatch):
        limiter = MessageRateLimiter(max_per_second=5, max_per_minute=15)

        fake_now = [1000.0]

        def _time():
            return fake_now[0]

        monkeypatch.setattr("time.time", _time)

        # Add some old entries
        limiter.global_history = [fake_now[0] - 120, fake_now[0] - 90]
        await limiter.wait()
        # Old entries should be cleaned up
        for t in limiter.global_history:
            assert fake_now[0] - t < 60
