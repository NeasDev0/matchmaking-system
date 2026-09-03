import asyncio
import random
import json
import httpx
import websockets
import os
API_URL = "http://localhost:8000"


async def api_request(client: httpx.AsyncClient, player_id: str, method: str, endpoint: str, data: dict = None, params: dict = None): #type: ignore
    url = f"{API_URL}{endpoint}"
    try:
        if method == "GET":
            response = await client.get(url, params=params)
        elif method == "POST":
            response = await client.post(url, json=data, params=params)
        
        response.raise_for_status()  #type: ignore
        return response.json() #type: ignore
    except httpx.HTTPStatusError as e:
        print(f"[{player_id}] Ошибка HTTP для {endpoint}: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        print(f"[{player_id}] Ошибка соединения: {e}")
    return None


async def connect_to_game_server(player_id: str, ip: str, port: int, token: str):
    ws_url = f"ws://{ip}:{port}"
    print(f"[{player_id}] 🔌 Подключаемся к сокету сервера {ws_url}...")
    
    try:
        async with websockets.connect(ws_url) as ws:
            hello_msg = {
                "type": "hello",
                "player_id": player_id,
                "token": token
            }
            await ws.send(json.dumps(hello_msg))
            
            async for message in ws:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == "welcome":
                    lobby = data.get("lobby_id")
                    print(f"[{player_id}] 🎮 Успешно зашли в лобби {lobby}.")
                
                elif msg_type == "tick":
                    remaining = data.get("remaining")
                    if remaining % 5 == 0 or remaining <= 3: 
                        print(f"[{player_id}] ⏳ До конца матча: {remaining} сек.")
                        
                elif msg_type == "error":
                    print(f"[{player_id}] ❌ Ошибка сервера: {data.get('reason')}")
                    break
                    
                elif msg_type == "match_ended":
                    print(f"[{player_id}] 🏁 Сервер сообщил об окончании матча!")
                    break
                    
    except websockets.exceptions.ConnectionClosed:
        print(f"[{player_id}] 🔌 Соединение закрыто сервером.")
    except Exception as e:
        print(f"[{player_id}] ❌ Ошибка связи с сервером: {e}")


async def run_single_bot(player_id: str, player_mmr: int):
    """Одноразовый бот: зашел -> отыграл -> умер."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        print(f"[{player_id}] 🚀 Рождение бота (MMR: {player_mmr}). Встаем в очередь...")
        res = await api_request(client, player_id, "POST", "/matchmaking/find", data={"player_id": player_id, "mmr": player_mmr})
        
        if not res:
            print(f"[{player_id}] 💀 Не удалось встать в очередь. Бот умирает.")
            return

        current_lobby_id = None

        # Опрашиваем статус до завершения игры
        while True:
            await asyncio.sleep(2)
            
            status_data = await api_request(client, player_id, "GET", "/matchmaking/status", params={"player_id": player_id})
            if not status_data:
                continue

            status = status_data.get("status")

            if status == "match_found":
                current_lobby_id = status_data.get("lobby_id")
                delay = random.uniform(0.5, 2.0)
                await asyncio.sleep(delay)

                accept_res = await api_request(
                    client, 
                    player_id, 
                    "POST", 
                    "/matchmaking/accept", 
                    data={"player_id": player_id, "lobby_id": current_lobby_id}
                )
                if accept_res:
                    print(f"[{player_id}] Матч принят, ждем начала...")

            elif status == "in_game":
                ip = status_data.get("ip")
                port = status_data.get("port")
                token = status_data.get("token")
                
                # Подключаемся и играем
                await connect_to_game_server(player_id, ip, port, token)
                
                # === БОТ ВЫПОЛНИЛ СВОЮ МИССИЮ И УМИРАЕТ ===
                print(f"[{player_id}] 💀 Бот отработал матч и самоликвидируется.")
                return


async def bot_manager(spawn_interval: float = 1.0):
    """Генератор ботов: постоянно создает новых одноразовых ботов."""
    bot_counter = 1
    print(f"[MANAGER] 🏭 Запуск генератора ботов (интервал спавна: {spawn_interval} сек)...")

    while True:
        player_id = f"bot_player_{bot_counter}"
        bot_counter += 1

        # asyncio.create_task отправляет бота выполняться в фоне
        # Менеджер не ждет завершения этого бота и сразу идет дальше!
        asyncio.create_task(run_single_bot(player_id, random.randint(0, 500)))

        # Пауза перед созданием СЛЕДУЮЩЕГО бота
        await asyncio.sleep(spawn_interval)


if __name__ == "__main__":
    try:
        # Спавним по 1 новому боту каждые 1.5 секунды
        asyncio.run(bot_manager(spawn_interval=2.0))
    except KeyboardInterrupt:
        print("\n[MANAGER] Генерация ботов остановлена.")
        os.system("cls" if os.name == "nt" else "clear")
        os._exit(0)  # принудительно завершаем все фоновые таски, чтобы не висели в фоне
        