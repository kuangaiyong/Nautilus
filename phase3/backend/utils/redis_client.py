"""
Redis client utility for caching and session management.
"""
import redis
import os
from typing import Optional

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Create Redis client
# protocol=2：本机 Redis 3.0.504 不支持 RESP3 的 HELLO，redis-py 8.x 默认 RESP3 会失败。
# from_url 路径（event_bus 等）通过 REDIS_URL 的 ?protocol=2 解决；此处离散参数需显式指定。
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    protocol=2,
)

def get_redis() -> redis.Redis:
    """Get Redis client instance."""
    return redis_client


async def get_redis_client():
    """Async-compatible Redis client (returns sync client wrapped for compatibility)."""
    try:
        redis_client.ping()
        return redis_client
    except Exception:
        return None


def test_redis_connection() -> bool:
    """Test Redis connection."""
    try:
        redis_client.ping()
        return True
    except Exception as e:
        print(f"Redis connection failed: {e}")
        return False
