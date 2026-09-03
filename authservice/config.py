"""Настройки сервиса. Всё, что раньше было россыпью констант и Path(...).read_text()
прямо в главном файле, теперь читается из окружения с разумными дефолтами —
и пути к ключам больше не зависят от того, из какой директории вы запустили uvicorn.
"""
import os
from pathlib import Path

# Корень пакета (authservice/), а не текущая рабочая директория процесса —
# иначе `uvicorn authservice.main:app` из другого cwd не находил private.pem.
BASE_DIR = Path(__file__).resolve().parent

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://user:password@localhost:5432/userdb"
)

PRIVATE_KEY_PATH = Path(os.getenv("JWT_PRIVATE_KEY_PATH", BASE_DIR / "keys" / "private.pem"))
PUBLIC_KEY_PATH = Path(os.getenv("JWT_PUBLIC_KEY_PATH", BASE_DIR / "keys" / "public.pem"))
JWT_ALGORITHM = "RS256"

ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30
