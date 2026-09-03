"""Хэширование паролей и всё, что касается JWT: выпуск access/refresh токенов
и зависимость get_current_user_id для защищённых эндпоинтов.
"""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from .config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    PRIVATE_KEY_PATH,
    PUBLIC_KEY_PATH,
    REFRESH_TOKEN_EXPIRE_DAYS,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def _read_key(path):
    if not path.exists():
        raise RuntimeError(
            f"Не найден ключ для JWT: {path}\n"
            f"Сгенерируйте пару RSA-ключей, например:\n"
            f"  openssl genrsa -out {path.parent / 'private.pem'} 2048\n"
            f"  openssl rsa -in {path.parent / 'private.pem'} -pubout -out {path.parent / 'public.pem'}"
        )
    return path.read_text()


# Ключи читаются один раз при импорте модуля — как и было в исходнике,
# просто с понятной ошибкой вместо голого FileNotFoundError, если их не сгенерировали.
PRIVATE_KEY = _read_key(PRIVATE_KEY_PATH)
PUBLIC_KEY = _read_key(PUBLIC_KEY_PATH)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "type": "access", "exp": expire}
    return jwt.encode(payload, PRIVATE_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "type": "refresh", "exp": expire}
    return jwt.encode(payload, PRIVATE_KEY, algorithm=JWT_ALGORITHM)


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> int:
    # security сделан с auto_error=False (см. коммент в роутере) — значит без
    # заголовка Authorization credentials будет None. В исходнике это не
    # проверялось, и credentials.credentials падало с AttributeError -> 500
    # вместо честного 401. Здесь это исправлено.
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = credentials.credentials
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms=[JWT_ALGORITHM])

        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")

        return int(payload.get("sub"))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
