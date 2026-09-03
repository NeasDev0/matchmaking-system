"""Единое подключение к Redis.

Раньше клиент жил как `global r` прямо в файле с эндпоинтами — это работало,
но означало, что и роутеры, и фоновые воркеры неявно зависели от того, что
lifespan успел отработать раньше них. Здесь то же самое поведение, просто
явно: `init_redis()` вызывается один раз в lifespan, все остальные модули
получают клиент через `get_redis()`.
"""
from redis import asyncio as aioredis

from .config import REDIS_HOST, REDIS_PORT

_redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    global _redis_client
    _redis_client = aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True
    )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def get_redis() -> aioredis.Redis:
    if _redis_client is None:
        raise RuntimeError(
            "Redis-клиент ещё не инициализирован — приложение стартовало "
            "не через lifespan из gateway.main"
        )
    return _redis_client
