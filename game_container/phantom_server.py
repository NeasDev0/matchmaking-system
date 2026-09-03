"""Фантомный игровой сервер — теперь с реальным сокетом.

Раньше этот процесс просто дёргал HTTP-эндпоинты гейтвея и делал вид, что
матч идёт (heartbeat со статусом in_game по таймеру). Теперь он ещё и
реально слушает свой PORT по WebSocket, принимает подключения от игровых
клиентов, проверяет их connect-токен (тот самый, что гейтвей выдал в ответе
на /server/confirm_match) и держит матч ровно MATCH_DURATION секунд,
рассылая подключенным клиентам обратный отсчёт.

Протокол очень простой, JSON-сообщения поверх WebSocket:

  Клиент -> Сервер (первым сообщением после открытия соединения):
    {"type": "hello", "player_id": "...", "token": "..."}

  Сервер -> Клиент:
    {"type": "error", "reason": "not_ready" | "invalid_token"}   (после чего close)
    {"type": "welcome", "lobby_id": ..., "match_duration": 30, "remaining": 30}
    {"type": "tick", "remaining": 27.3}                           (раз в TICK_INTERVAL)
    {"type": "match_ended"}                                       (после чего close)

Зависимости: добавьте `websockets` рядом с `httpx` в окружение/образ
(pip install websockets httpx). Порт уже проброшен наружу node_manager'ом
и по tcp, и по udp — WebSocket работает поверх tcp, ничего в докер-конфиге
менять не нужно.
"""
import asyncio
import json
import os
import sys
import time
import uuid

import httpx
import websockets

PORT = int(os.getenv("SERVER_PORT", "7000"))
PUBLIC_IP = os.getenv("PUBLIC_IP", "127.0.0.1")
MASTER_URL = os.getenv("MASTER_SERVER_URL", "http://host.docker.internal:8000")
# Уникальный суффикс на каждый запуск — иначе при переиспользовании порта
# новый контейнер получит тот же server_id, что и предыдущий, и может
# зацепить его недочищенные записи в Redis (assigned_lobby, старые токены и т.д.)
SERVER_ID = f"phantom_srv_{PORT}_{uuid.uuid4().hex[:6]}"

ASSIGNMENT_POLL_INTERVAL = 2.0   # как часто спрашиваем, назначили ли нам лобби
ASSIGNMENT_WAIT_TIMEOUT = 20.0   # сколько ждём назначения, прежде чем сдаться
MATCH_DURATION = 30.0            # сколько реально идёт матч (короткое значение — для теста)
HEARTBEAT_INTERVAL = 5.0
TICK_INTERVAL = 1.0              # как часто шлём countdown подключенным игрокам

# ---------- общее состояние матча (один процесс = один матч за жизнь контейнера) ----------
connect_tokens: dict[str, str] = {}          # player_id -> token, заполняется после confirm_match
connected_players: dict[str, websockets.ServerConnection] = {}
match_ready = asyncio.Event()                # взводится, когда confirm_match прошёл
match_end_time: float | None = None
current_lobby_id: str | None = None


async def register(client: httpx.AsyncClient) -> bool:
    try:
        await client.post(
            f"{MASTER_URL}/server/register",
            json={"server_id": SERVER_ID, "ip": PUBLIC_IP, "port": PORT, "max_players": 10},
            timeout=5.0,
        )
        print(f"[Phantom {PORT}] Зарегистрирован как {SERVER_ID}, статус waiting")
        return True
    except Exception as e:
        print(f"[Phantom {PORT}] Ошибка регистрации: {e}")
        return False


async def wait_for_assignment(client: httpx.AsyncClient) -> str | None:
    """Поллит /server/assignment, пока матчмейкер не назначит лобби на этот сервер."""
    print(f"[Phantom {PORT}] Жду назначения лобби от матчмейкера...")
    start = time.time()

    while time.time() - start < ASSIGNMENT_WAIT_TIMEOUT:
        try:
            resp = await client.get(
                f"{MASTER_URL}/server/assignment",
                params={"server_id": SERVER_ID},
                timeout=2.0,
            )
            data = resp.json()
            if data.get("assigned"):
                return data["lobby_id"]
            print(f"[Phantom {PORT}] assignment: пока не назначено")
        except Exception as e:
            print(f"[Phantom {PORT}] Ошибка поллинга assignment: {e}")

        # Пока не назначили — держим статус waiting, чтобы TTL не сгорел
        try:
            await client.post(
                f"{MASTER_URL}/server/heartbeat",
                json={"server_id": SERVER_ID, "current_players": 0, "status": "waiting"},
                timeout=1.0,
            )
        except Exception:
            pass

        await asyncio.sleep(ASSIGNMENT_POLL_INTERVAL)

    return None


async def confirm_match(client: httpx.AsyncClient, lobby_id: str) -> dict[str, str] | None:
    try:
        resp = await client.post(
            f"{MASTER_URL}/server/confirm_match",
            json={"server_id": SERVER_ID, "lobby_id": lobby_id},
            timeout=2.0,
        )
        if resp.status_code == 200:
            tokens = resp.json().get("connect_tokens", {})
            print(f"[Phantom {PORT}] Матч {lobby_id} подтверждён, получено {len(tokens)} токенов")
            return tokens
        print(f"[Phantom {PORT}] confirm_match отклонён: {resp.status_code} {resp.text}")
        return None
    except Exception as e:
        print(f"[Phantom {PORT}] Ошибка confirm_match: {e}")
        return None


# ---------- WebSocket-часть: сюда реально подключаются игровые клиенты ----------

async def handle_client(websocket):
    """Одно подключение игрока. Первым сообщением клиент обязан прислать
    {"type":"hello","player_id":...,"token":...} — токен он получает от
    гейтвея через /matchmaking/status, когда его статус становится in_game."""
    peer = websocket.remote_address

    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        return

    try:
        hello = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.close(code=4000, reason="bad_request")
        return

    player_id = hello.get("player_id")
    token = hello.get("token")

    if not match_ready.is_set():
        await websocket.send(json.dumps({"type": "error", "reason": "not_ready"}))
        await websocket.close(code=4000, reason="not_ready")
        return

    if not player_id or connect_tokens.get(player_id) != token:
        print(f"[Phantom {PORT}] Отклонено подключение с невалидным токеном: {peer}")
        await websocket.send(json.dumps({"type": "error", "reason": "invalid_token"}))
        await websocket.close(code=4001, reason="invalid_token")
        return

    connected_players[player_id] = websocket
    remaining = max(0.0, round(match_end_time - time.time(), 1)) if match_end_time else MATCH_DURATION
    print(f"[Phantom {PORT}] Игрок {player_id} подключился к матчу {current_lobby_id}")

    await websocket.send(json.dumps({
        "type": "welcome",
        "lobby_id": current_lobby_id,
        "match_duration": MATCH_DURATION,
        "remaining": remaining,
    }))

    try:
        async for _ in websocket:
            pass  # от клиента содержательно ничего не ждём, просто держим соединение живым
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if connected_players.get(player_id) is websocket:
            del connected_players[player_id]
        print(f"[Phantom {PORT}] Игрок {player_id} отключился")


async def broadcast_ticks():
    """Раз в TICK_INTERVAL шлёт всем подключенным игрокам оставшееся время матча,
    а по истечении MATCH_DURATION — общее оповещение и закрывает соединения."""
    await match_ready.wait()

    while match_end_time is not None and time.time() < match_end_time:
        remaining = round(match_end_time - time.time(), 1)
        payload = json.dumps({"type": "tick", "remaining": remaining})
        for ws in list(connected_players.values()):
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                pass
        await asyncio.sleep(TICK_INTERVAL)

    for ws in list(connected_players.values()):
        try:
            await ws.send(json.dumps({"type": "match_ended"}))
            await ws.close(code=1000, reason="match_ended")
        except websockets.exceptions.ConnectionClosed:
            pass


async def run_match(client: httpx.AsyncClient, lobby_id: str):
    """Держит heartbeat со статусом in_game, пока не пройдёт MATCH_DURATION секунд
    с момента confirm_match — эта константа теперь напрямую определяет, сколько
    реально живут WebSocket-подключения игроков."""
    global match_end_time
    match_end_time = time.time() + MATCH_DURATION
    match_ready.set()

    while time.time() < match_end_time:
        await asyncio.sleep(min(HEARTBEAT_INTERVAL, max(0.0, match_end_time - time.time())))
        try:
            await client.post(
                f"{MASTER_URL}/server/heartbeat",
                json={
                    "server_id": SERVER_ID,
                    "current_players": len(connected_players),
                    "status": "in_game",
                },
                timeout=1.0,
            )
        except Exception:
            pass
        elapsed = MATCH_DURATION - max(0.0, match_end_time - time.time())
        print(f"[Phantom {PORT}] Матч {lobby_id} идёт... "
              f"({elapsed:.0f}/{MATCH_DURATION:.0f} сек, подключено игроков: {len(connected_players)})")

    print(f"[Phantom {PORT}] Матч {lobby_id} окончен.")


async def main():
    global current_lobby_id
    print(f"[Phantom {PORT}] Запущен!")

    async with httpx.AsyncClient() as client:
        # Поднимаем сокет сразу, ещё до того как матч вообще нашёлся — порт должен
        # быть готов принимать TCP-соединения с первой секунды жизни контейнера.
        # Клиенты, постучавшиеся раньше времени, получат not_ready и отвалятся —
        # это ожидаемо, реальный клиент так рано и не должен стучаться.
        ws_server = await websockets.serve(handle_client, "0.0.0.0", PORT)
        tick_task = asyncio.create_task(broadcast_ticks())

        if not await register(client):
            ws_server.close()
            sys.exit(1)

        lobby_id = await wait_for_assignment(client)
        if not lobby_id:
            print(f"[Phantom {PORT}] Назначения не дождался, завершаюсь.")
            ws_server.close()
            sys.exit(0)

        current_lobby_id = lobby_id

        tokens = await confirm_match(client, lobby_id)
        if tokens is None:
            # Не смогли подтвердить — сервер не должен молча делать вид, что матч
            # идёт. Просто завершаемся, reaper на стороне gateway вернёт игроков в очередь.
            ws_server.close()
            sys.exit(1)

        connect_tokens.update(tokens)

        await run_match(client, lobby_id)

        tick_task.cancel()
        ws_server.close()
        await ws_server.wait_closed()

    # Одноразовый контейнер: умираем, Docker (auto_remove=True) уберёт контейнер,
    # node_manager поднимет новый на освободившемся месте.
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())