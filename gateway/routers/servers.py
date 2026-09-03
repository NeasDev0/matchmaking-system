"""Эндпоинты для игровых серверов (то, с чем говорит phantom_server.py):
регистрация, heartbeat, поллинг назначения лобби, подтверждение матча.

Здесь нет ничего про очередь игроков или accept — это matchmaking.py.
"""
import json
import time
import uuid

from fastapi import APIRouter, HTTPException

from ..config import RESERVE_TTL, SERVERS_AVAILABLE_KEY, SERVERS_RESERVED_KEY
from ..models import ConfirmMatchRequest, ServerHeartbeatRequest, ServerRegisterRequest
from ..state import get_redis

router = APIRouter(prefix="/server", tags=["servers"])


@router.post("/register")
async def register_server(payload: ServerRegisterRequest):
    r = get_redis()
    redis_key = f"server:{payload.server_id}"
    expire_at = time.time() + 15  # Умрёт через 15 секунд

    await r.hset(
        redis_key,
        mapping={
            "ip": str(payload.ip),
            "port": str(payload.port),
            "max_players": str(payload.max_players),
            "current_players": "0",
            "status": "waiting",
        },
    )
    await r.expire(redis_key, 10)

    # Добавляем в Sorted Set со score = время истечения
    await r.zadd(SERVERS_AVAILABLE_KEY, {payload.server_id: expire_at})

    return {"status": "registered", "server_id": payload.server_id}


@router.post("/heartbeat")
async def server_heartbeat(payload: ServerHeartbeatRequest):
    r = get_redis()
    redis_key = f"server:{payload.server_id}"

    # 1. Проверяем существование сервера
    exists = await r.exists(redis_key)
    if not exists:
        raise HTTPException(
            status_code=404,
            detail="Сервер не найден или истек его TTL. Зарегистрируйтесь снова.",
        )

    # 2. Обновляем данные сервера
    await r.hset(
        redis_key,
        mapping={
            "current_players": str(payload.current_players),
            "status": payload.status,
        },
    )

    now = time.time()

    # 3. Синхронизируем пулы и TTL в зависимости от статуса
    if payload.status == "waiting":
        ttl = 10
        new_expire_at = now + ttl

        # Добавляем в доступные, удаляем из резерва
        await r.zadd(SERVERS_AVAILABLE_KEY, {payload.server_id: new_expire_at})
        await r.zrem(SERVERS_RESERVED_KEY, payload.server_id)

    elif payload.status == "reserved":
        ttl = RESERVE_TTL + 5  # Даем небольшой запас, чтобы основной ключ не сгорел раньше резерва
        new_expire_at = now + RESERVE_TTL

        # Удаляем из доступных, добавляем в резерв
        await r.zrem(SERVERS_AVAILABLE_KEY, payload.server_id)
        await r.zadd(SERVERS_RESERVED_KEY, {payload.server_id: new_expire_at})

    else:
        # Статусы "in_game", "stopping" и т.д.
        ttl = 15
        # Сервер занят матчем — он не должен быть ни в доступных, ни в зарезервированных
        await r.zrem(SERVERS_AVAILABLE_KEY, payload.server_id)
        await r.zrem(SERVERS_RESERVED_KEY, payload.server_id)

    # 4. Обновляем TTL самого Hash-ключа под актуальный статус
    await r.expire(redis_key, ttl)

    return {"status": "heartbeat received", "server_id": payload.server_id}


@router.get("/assignment")
async def get_assignment(server_id: str):
    """Игровой сервер поллит этот эндпоинт, чтобы узнать, назначили ли ему лобби."""
    r = get_redis()
    server_key = f"server:{server_id}"
    if not await r.exists(server_key):
        raise HTTPException(status_code=404, detail="Сервер не зарегистрирован")

    lobby_id = await r.hget(server_key, "assigned_lobby")
    if not lobby_id:
        return {"assigned": False}

    return {"assigned": True, "lobby_id": lobby_id}


@router.post("/confirm_match")
async def confirm_match(payload: ConfirmMatchRequest):
    """Сервер подтверждает, что матч реально поднялся — только теперь выдаём игрокам connect info."""
    r = get_redis()
    server_key = f"server:{payload.server_id}"
    server = await r.hgetall(server_key)

    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    if server.get("assigned_lobby") != payload.lobby_id:
        raise HTTPException(status_code=409, detail="Лобби не совпадает с назначенным")

    reservation_raw = await r.get(f"reservation:{payload.lobby_id}")
    if not reservation_raw:
        raise HTTPException(status_code=410, detail="Резервация истекла")

    players_mmr = json.loads(reservation_raw)

    # Генерируем одноразовый connect-токен на каждого игрока. Игрок получает
    # его через player_status и предъявляет при подключении к игровому серверу;
    # сервер сверяет с тем, что получил здесь же в ответе — без токена подключиться
    # напрямую по IP:port, зная только чужой player_id, не получится.
    connect_tokens = {p_id: uuid.uuid4().hex for p_id in players_mmr}

    for p_id, token in connect_tokens.items():
        await r.set(
            f"player_status:{p_id}",
            json.dumps(
                {
                    "status": "in_game",
                    "lobby_id": payload.lobby_id,
                    "ip": server["ip"],
                    "port": server["port"],
                    "token": token,
                }
            ),
        )

    # Валидные токены для этого сервера — сам сервер будет сверять их при коннекте
    await r.hset(f"server_tokens:{payload.server_id}", mapping=connect_tokens)
    await r.expire(f"server_tokens:{payload.server_id}", 3600)

    await r.hset(server_key, "status", "in_game")
    await r.zrem(SERVERS_RESERVED_KEY, payload.server_id)
    await r.delete(f"reservation:{payload.lobby_id}")
    await r.delete(f"lobby:{payload.lobby_id}")

    return {"status": "confirmed", "connect_tokens": connect_tokens}
