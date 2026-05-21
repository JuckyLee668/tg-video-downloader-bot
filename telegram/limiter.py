import asyncio
import time


class MessageRateLimiter:
    def __init__(self, max_per_second: int = 5, max_per_minute: int = 15):
        self.max_per_second = max_per_second
        self.max_per_minute = max_per_minute
        self.global_history = []
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = time.time()

            # Clean up old history
            self.global_history = [t for t in self.global_history if now - t < 60]

            # Check per-second limit
            per_second = [t for t in self.global_history if now - t < 1]
            if len(per_second) >= self.max_per_second:
                await asyncio.sleep(1 - (now - per_second[0]))
                now = time.time()
                self.global_history = [t for t in self.global_history if now - t < 60]

            # Check per-minute limit
            if len(self.global_history) >= self.max_per_minute:
                await asyncio.sleep(60 - (now - self.global_history[0]))
                now = time.time()

            self.global_history.append(now)

rate_limiter = MessageRateLimiter()
