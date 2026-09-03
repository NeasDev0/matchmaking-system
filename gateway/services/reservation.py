import json
import time

from fastapi import HTTPException

from ..config import RESERVE_TTL, SERVERS_AVAILABLE_KEY, SERVERS_RESERVED_KEY
from ..state import get_redis


async def reserve_server(lobby_id: str, players_mmr: dict[str, int]):
    """Атомарно достаёт один свободный сервер из пула и привязывает к лобби."""
    r = get_redis()

    await r.zremrangebyscore(SERVERS_AVAILABLE_KEY, 0, time.time() - 1)  # чистим просроченные записи
    popped = await r.zpopmin(SERVERS_AVAILABLE_KEY, 1)
    if not popped:
        raise HTTPException(status_code=503, detail="Нет свободных серверов")

    # zpopmin возвращает список кортежей: [(b'server_123', 1710000000.0)]
    server_id = popped[0][0]

    server_key = f"server:{server_id}"
    print(f"[Reserve Server] Сервер {server_id} зарезервирован для лобби {lobby_id}. Игроки: {players_mmr}")
    await r.hset(server_key, mapping={"status": "reserved", "assigned_lobby": lobby_id})
    await r.expire(server_key, RESERVE_TTL + 5)

    # Кладём в ZSET с временем истечения — reaper проверяет по нему
    await r.zadd(SERVERS_RESERVED_KEY, {server_id: time.time() + RESERVE_TTL})  # type: ignore

    # Сохраняем состав лобби отдельно — понадобится, если сервер не подтвердит матч
    await r.set(f"reservation:{lobby_id}", json.dumps(players_mmr), ex=RESERVE_TTL + 10)

    return {"server_id": server_id}
