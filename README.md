# 🚀 Matchmaking System

**Распределённая система матчмейкинга с микросервисной архитектурой, динамическим управлением игровыми серверами через Docker и WebSocket-связью.**

## 🧠 Общая архитектура

*   **Gateway** (`gateway/`) — API-шлюз на FastAPI. Принимает запросы от игроков, обрабатывает матчмейкинг (очереди в Redis) и управляет серверами.
*   **Auth Service** (`authservice/`) — микросервис авторизации и аутентификации (JWT + RSA-ключи).
*   **Node Manager** (`game_container/node_manager.py`) — оркестратор на Python, который управляет Docker-контейнерами игровых серверов.
*   **Game Server** (`game_container/phantom_server.py`) — **игровой сервер на Python (WebSockets)**. Запускается в Docker-контейнере и обрабатывает игровую логику (в будущем планируется переписывание на C++).
*   **Testing bots** (`test_bots.py`) — тестовый скрипт, который имитирует игроков для проверки системы.

## ⚙️ Технологический стек

*   **Backend:** Python (FastAPI, Uvicorn, Redis)
*   **Database/Queue:** Redis, PostgreSQL (для Auth Service)
*   **Containerization:** Docker, Docker SDK for Python
*   **Security:** JWT, RSA-2048, bcrypt

## 🚀 Как запустить проект

### 1️⃣ Клонируй репозиторий
```bash
git clone https://github.com/NeasDev0/matchmaking-system.git
cd matchmaking-system
2️⃣ Сгенерируй ключи для JWT
Перейди в папку authservice/keys и сгенерируй RSA-ключи:

bash
cd authservice/keys
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
cd ../..
3️⃣ Запусти все сервисы через Docker Compose
bash
docker-compose up -d --build
4️⃣ Проверь, что всё работает
Открой в браузере:

Gateway: http://localhost:8000/docs

Auth Service: http://localhost:8001/docs

5️⃣ Запусти тестовых ботов (для демонстрации)
bash
python test_bots.py
6️⃣ Открой Dashboard для мониторинга
Открой gateway/client.html через Live Server (или просто открой в браузере, если у тебя настроен CORS).

🐳 Структура проекта
/authservice — Микросервис аутентификации

/gateway — API-шлюз и матчмейкинг

/game_container — Игровой сервер на Python и менеджер Docker-контейнеров

test_bots.py — Скрипт для нагрузочного тестирования

🖥️ Для пользователей Windows
Если вы используете Windows, убедитесь, что в docker-compose.yml для node_manager правильно указан сокет Docker (для WSL2 используется /var/run/docker.sock, как в репозитории).

🛠️ Полезные команды
Остановить всё: docker-compose down

Пересобрать и перезапустить: docker-compose up -d --build

Посмотреть логи Gateway: docker logs gateway -f

Посмотреть логи Node Manager: docker logs node_manager -f

📌 Важно
Проект использует phantom_server.py на Python, который в будущем планируется переписать на C++ для улучшения производительности.

Для корректной работы Node Manager в Windows с WSL2 необходимо пробросить сокет Docker (/var/run/docker.sock).