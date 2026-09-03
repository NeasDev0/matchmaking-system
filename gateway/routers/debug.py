"""Отладочные/дашбордовые эндпоинты — снимки состояния очереди, лобби и
серверов. Не для боевого клиента и не для игрового сервера."""
import time

from fastapi import APIRouter, HTTPException

from ..config import SERVERS_AVAILABLE_KEY, SERVERS_RESERVED_KEY
from ..state import get_redis

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/lobby/{lobby_id}")
async def debug_lobby(lobby_id: str):
    """Только для тестирования: показывает реальный состав лобби и статусы accept."""
    r = get_redis()
    members = await r.hgetall(f"lobby:{lobby_id}")
    if not members:
        raise HTTPException(status_code=404, detail="Лобби не найдено")
    return {"lobby_id": lobby_id, "members": members}


@router.get("/queue")
async def debug_queue():
    """Снимок очереди матчмейкинга — для дашборда, не для боевого клиента."""
    r = get_redis()
    players = await r.zrange("queue:ranked", 0, -1, withscores=True)
    now = time.time()
    result = []

    for player_id, mmr in players:
        joined_raw = await r.get(f"queue_time:{player_id}")
        wait_seconds = round(now - float(joined_raw), 1) if joined_raw else None
        result.append({"player_id": player_id, "mmr": int(mmr), "wait_seconds": wait_seconds})

    result.sort(key=lambda p: p["wait_seconds"] or 0, reverse=True)
    return {"count": len(result), "players": result}


@router.get("/servers")
async def debug_servers():
    """Снимок состояния всех известных серверов: waiting / reserved / in_game.
    Использует SCAN, не KEYS — безопасно даже при заметном числе серверов."""
    r = get_redis()
    servers = []
    async for key in r.scan_iter(match="server:*"):
        data = await r.hgetall(key)
        if not data:
            continue
        data["server_id"] = key.split("server:", 1)[1]
        servers.append(data)

    await r.zremrangebyscore(SERVERS_AVAILABLE_KEY, 0, time.time() - 1)  # чистим просроченные записи
    available_count = await r.zcard(SERVERS_AVAILABLE_KEY)
    reserved_raw = await r.zrange(SERVERS_RESERVED_KEY, 0, -1, withscores=True)
    now = time.time()
    reserved = [
        {"server_id": sid, "expires_in": round(expire_at - now, 1)}  # type: ignore
        for sid, expire_at in reserved_raw
    ]

    return {
        "servers": servers,
        "available_count": available_count,
        "reserved": reserved,
    }


@router.get("/lobbies")
async def debug_lobbies():
    """Снимок всех активных лобби (ещё не подтверждённых сервером и не истёкших)."""
    r = get_redis()
    lobbies = []
    async for key in r.scan_iter(match="lobby:*"):
        members = await r.hgetall(key)
        if not members:
            continue
        lobby_id = key.split("lobby:", 1)[1]
        accepted = sum(1 for s in members.values() if s == "accepted")
        lobbies.append({
            "lobby_id": lobby_id,
            "members": members,
            "accepted_count": accepted,
            "total": len(members),
        })
    return {"count": len(lobbies), "lobbies": lobbies}


@router.post("/reset")
async def debug_reset():
    """Только для тестирования: полностью чистит очередь, лобби и резервации серверов.
    Сами зарегистрированные сервера не трогает — они сами пришлют новый heartbeat waiting."""
    r = get_redis()
    deleted_keys = 0

    await r.delete("queue:ranked")

    async for key in r.scan_iter(match="queue_time:*"):
        await r.delete(key)
        deleted_keys += 1

    async for key in r.scan_iter(match="player_status:*"):
        await r.delete(key)
        deleted_keys += 1

    async for key in r.scan_iter(match="lobby:*"):
        await r.delete(key)
        deleted_keys += 1

    async for key in r.scan_iter(match="reservation:*"):
        await r.delete(key)
        deleted_keys += 1

    await r.delete(SERVERS_RESERVED_KEY)

    # Сервера возвращаем в waiting принудительно, чтобы не ждать их следующего heartbeat
    async for key in r.scan_iter(match="server:*"):
        server_id = key.split("server:", 1)[1]
        await r.hset(key, mapping={"status": "waiting", "assigned_lobby": "", "current_players": "0"})
        await r.expire(key, 10)
        await r.zadd(SERVERS_AVAILABLE_KEY, {server_id: time.time() + 10})

    return {"status": "reset", "cleared_keys": deleted_keys}
