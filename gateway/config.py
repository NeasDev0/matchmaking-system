"""Настройки и константы гейтвея. Ничего исполняемого — только значения,
которые нужны и роутерам, и фоновым воркерам."""
import os

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# --- Матчмейкинг по MMR ---
BASE_MMR_RANGE = 50         # Начальный допустимый разброс (±50 MMR)
RANGE_GROWTH_PER_SEC = 10   # Каждую секунду ожидания диапазон растет на ±10 MMR
MAX_MMR_RANGE = 1000         # Максимальный разброс (±1000 MMR)
TEAM_SIZE = 10

# --- Тайминги лобби / резервации сервера ---
LOBBY_ACCEPT_TIMEOUT = 20.0  # сколько ждём accept от всех 10 игроков
RESERVE_TTL = 15              # сколько ждём confirm_match от игрового сервера

# --- Ключи Redis ---
SERVERS_AVAILABLE_KEY = "servers:available"  # ZSET server_id -> expire_at, свободные сервера
SERVERS_RESERVED_KEY = "servers:reserved"    # ZSET server_id -> expire_at (для reaper)
