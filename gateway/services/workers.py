"""Три фоновые задачи, которые крутятся всё время жизни приложения:

- matchmaking_worker  — раз в секунду ищет 10 игроков с подходящим MMR и собирает лобби
- verify_lobby_timeout — разовая задача на каждое лобби, разруливает недобранный accept
- server_reaper        — раз в 2 секунды проверяет зависшие резервации серверов

Все три запускаются из lifespan в main.py.
"""
import asyncio
import json
import time
import uuid

from ..config import (
    BASE_MMR_RANGE,
    LOBBY_ACCEPT_TIMEOUT,
    MAX_MMR_RANGE,
    RANGE_GROWTH_PER_SEC,
    RESERVE_TTL,
    SERVERS_RESERVED_KEY,
    TEAM_SIZE,
)
from ..state import get_redis


async def matchmaking_worker():
    print("[Matchmaker Worker] Запущен умный подбор по MMR...")
    r = get_redis()

    while True:
        try:
            queue_players = await r.zrange("queue:ranked", 0, -1)
            current_time = time.time()

            for player_id in queue_players:
                
                player_mmr = await r.zscore("queue:ranked", player_id)  # type: ignore
                joined_time_raw = await r.get(f"queue_time:{player_id}")

                if player_mmr is None or joined_time_raw is None:
                    continue

                player_mmr = int(player_mmr)
                wait_time = current_time - float(joined_time_raw)

                dynamic_range = min(
                    BASE_MMR_RANGE + int(wait_time * RANGE_GROWTH_PER_SEC), MAX_MMR_RANGE
                )
                min_mmr = max(player_mmr - dynamic_range, 0)
                max_mmr = player_mmr + dynamic_range

                candidates_with_scores = await r.zrangebyscore(
                    "queue:ranked", min_mmr, max_mmr, withscores=True
                )

                if len(candidates_with_scores) >= TEAM_SIZE:
                    selected_data = candidates_with_scores[:TEAM_SIZE]
                    players_mmr_dict = {str(p[0]): int(p[1]) for p in selected_data}
                    selected_players = list(players_mmr_dict.keys())

                    # ФИКС гонки: убираем игроков из очереди и сверяем, что
                    # реально удалили ровно столько, сколько выбрали. Если кого-то
                    # уже увела параллельная итерация — не создаём лобби сейчас,
                    # просто ждём следующего прохода цикла.
                    removed = await r.zrem("queue:ranked", *selected_players)
                    if removed != len(selected_players):
                        for p_id in selected_players:
                            # Важно: это сработает, только если не было параллельного удаления другим сервером
                            await r.zadd("queue:ranked", {p_id: players_mmr_dict[p_id]})
                        continue

                    for p_id in selected_players:
                        await r.delete(f"queue_time:{p_id}")

                    lobby_id = str(uuid.uuid4())[:8]
                    lobby_key = f"lobby:{lobby_id}"

                    mapping = {p_id: "pending" for p_id in selected_players}
                    await r.hset(lobby_key, mapping=mapping)  # type: ignore
                    await r.set(
                        f"lobby_mmr:{lobby_id}",
                        json.dumps(players_mmr_dict),
                        ex=int(LOBBY_ACCEPT_TIMEOUT + RESERVE_TTL),  # Даем время на принятие и резервацию
                    )

                    await r.expire(lobby_key, int(LOBBY_ACCEPT_TIMEOUT) + 5)

                    asyncio.create_task(
                        verify_lobby_timeout(lobby_id, players_mmr_dict, delay=LOBBY_ACCEPT_TIMEOUT)
                    )

                    for p_id in selected_players:
                        await r.set(
                            f"player_status:{p_id}",
                            json.dumps({"status": "match_found", "lobby_id": lobby_id}),
                        )

                    
                    break

        except Exception as e:
            print(f"[Matchmaker Worker Error] {e}")

        await asyncio.sleep(1)


async def verify_lobby_timeout(lobby_id: str, players_mmr_dict: dict, delay: float = 20.0):
    await asyncio.sleep(delay)
    r = get_redis()
    lobby_key = f"lobby:{lobby_id}"

    lobby_responses = await r.hgetall(lobby_key)
    if not lobby_responses:
        return  # Лобби уже успешно удалено процессом accept

    accepted_players = [p for p, s in lobby_responses.items() if s == "accepted"]
    declined_players = [p for p, s in lobby_responses.items() if s != "accepted"]

    if len(accepted_players) < TEAM_SIZE:
        # Возвращаем в очередь тех, кто принял
        for p_id in accepted_players:
            await r.zadd("queue:ranked", {p_id: players_mmr_dict[p_id]})  # type: ignore
            await r.set(f"player_status:{p_id}", json.dumps({"status": "searching"}))
            await r.set(f"queue_time:{p_id}", time.time() - 30)

        # Снимаем статус с тех, кто не принял
        for p_id in declined_players:
            await r.delete(f"player_status:{p_id}")

    await r.delete(lobby_key)


async def server_reaper():
    """Раз в 2 секунды проверяет, не истекло ли время резервации сервера без confirm_match.
    Если сервер не подтвердил матч вовремя — освобождаем игроков обратно в очередь
    и удаляем зависшую запись сервера (он должен перерегистрироваться сам)."""
    
    r = get_redis()

    while True:
        try:
            now = time.time()
            expired = await r.zrangebyscore(SERVERS_RESERVED_KEY, 0, now)

            for server_id in expired:
                await r.zrem(SERVERS_RESERVED_KEY, server_id)  # type: ignore

                server_key = f"server:{server_id}"
                status = await r.hget(server_key, "status")
                lobby_id = await r.hget(server_key, "assigned_lobby")

                if status != "reserved":
                    continue  # сервер уже успел подтвердить матч между чтением и обработкой

                

                if lobby_id:
                    reservation_raw = await r.get(f"reservation:{lobby_id}")
                    if reservation_raw:
                        players_mmr = json.loads(reservation_raw)
                        for p_id, mmr in players_mmr.items():
                            await r.zadd("queue:ranked", {p_id: mmr})
                            await r.set(f"player_status:{p_id}", json.dumps({"status": "searching"}))
                            await r.set(f"queue_time:{p_id}", time.time() - 30)
                        await r.delete(f"reservation:{lobby_id}")

                # Сервер не выполнил обещание вовремя — считаем его подозрительным,
                # выкидываем из реестра, он должен зарегистрироваться заново сам.
                await r.delete(server_key)

        except Exception as e:
            print(f"[Reaper Error] {e}")

        await asyncio.sleep(2)
