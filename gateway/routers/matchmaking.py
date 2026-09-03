"""Эндпоинты для игрового клиента: встать/выйти из очереди, опросить статус,
принять найденный матч.

Здесь нет ничего про игровые сервера — это servers.py.
"""
import json
import time

from fastapi import APIRouter, HTTPException

from ..config import TEAM_SIZE
from ..models import AcceptMatchRequest, FindMatchRequest
from ..services.reservation import reserve_server
from ..state import get_redis

router = APIRouter(prefix="/matchmaking", tags=["matchmaking"])


@router.post("/find")
async def find_match(payload: FindMatchRequest):
    r = get_redis()
    await r.zadd("queue:ranked", {payload.player_id: payload.mmr})
    await r.set(f"queue_time:{payload.player_id}", time.time())
    await r.set(f"player_status:{payload.player_id}", json.dumps({"status": "searching"}))
    return {"status": "searching", "player_id": payload.player_id}


@router.post("/cancel")
async def cancel_find_match(player_id: str):
    r = get_redis()
    await r.zrem("queue:ranked", player_id)
    await r.delete(f"queue_time:{player_id}")
    await r.delete(f"player_status:{player_id}")
    return {"status": "cancelled", "player_id": player_id}


@router.get("/status")
async def matchmaking_status(player_id: str):
    r = get_redis()
    status = await r.get(f"player_status:{player_id}")
    if not status:
        raise HTTPException(status_code=404, detail="Player not found or not searching for a match.")
    return json.loads(status)


@router.post("/accept")
async def accept_match(payload: AcceptMatchRequest):
    r = get_redis()
    lobby_key = f"lobby:{payload.lobby_id}"

    if not await r.exists(lobby_key):
        raise HTTPException(status_code=404, detail="Лобби не найдено или время вышло")

    # Обновляем статус игрока
    await r.hset(lobby_key, payload.player_id, "accepted")
    await r.set(
        f"player_status:{payload.player_id}",
        json.dumps({"status": "accepted", "lobby_id": payload.lobby_id}),
    )

    # ПРОВЕРЯЕМ СТАТУС ВСЕХ В ЛОББИ СРАЗУ
    lobby_responses = await r.hgetall(lobby_key)
    accepted_count = sum(1 for status in lobby_responses.values() if status == "accepted")

    if accepted_count == TEAM_SIZE:
        print(f"[Accept Endpoint] Все игроки приняли матч {payload.lobby_id}! Стартуем.")
        players_mmr_raw = await r.get(f"lobby_mmr:{payload.lobby_id}")

        if players_mmr_raw:
            players_mmr_dict = json.loads(players_mmr_raw)
            try:
                await reserve_server(payload.lobby_id, players_mmr_dict)
                print(f"[Accept Endpoint] Сервер для лобби {payload.lobby_id} успешно зарезервирован.")
            except Exception as e:
                print(f"Ошибка резервации сервера: {e}")

    return {"status": "accepted"}
