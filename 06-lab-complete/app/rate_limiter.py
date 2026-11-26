"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis (sorted set) → mọi worker / instance dùng chung, còn sau restart
"""
import hashlib
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    # Không lưu API key gốc trong Redis, chỉ lưu hash.
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
        pipe.zcard(bucket)
        _, count = pipe.execute()
        if count >= limit:
            oldest = r.zrange(bucket, 0, 0, withscores=True)
            retry = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s",
                headers={"Retry-After": str(max(retry, 1))},
            )
        pipe = r.pipeline()
        pipe.zadd(bucket, {f"{now}:{uuid.uuid4().hex}": now})
        pipe.expire(bucket, WINDOW_SECONDS)
        pipe.execute()
    except redis.RedisError:
        # Không có Redis thì không đếm được → từ chối thay vì cho qua không giới hạn.
        raise HTTPException(status_code=503, detail="Rate limiter unavailable")
