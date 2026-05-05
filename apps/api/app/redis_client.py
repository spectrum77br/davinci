from redis.asyncio import Redis, from_url

from app.config import get_settings

_settings = get_settings()

redis: Redis = from_url(_settings.redis_url, decode_responses=True)
