import time

from app.redis_client import redis


class RateLimitError(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


async def sliding_window_check(*, key: str, limit: int, window_seconds: int) -> int:
    """Sliding window in Redis using sorted set timestamps.

    Returns number of remaining slots (>= 0). Raises RateLimitError if exceeded.
    """
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - window_seconds * 1000
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, cutoff)
    pipe.zadd(key, {f"{now_ms}:{now_ms}": now_ms})
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    _, _, count, _ = await pipe.execute()
    if count > limit:
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        retry_after = window_seconds
        if oldest:
            retry_after = max(1, int((oldest[0][1] + window_seconds * 1000 - now_ms) / 1000))
        raise RateLimitError(retry_after=retry_after)
    return max(0, limit - count)
