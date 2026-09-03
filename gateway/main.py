import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import debug, matchmaking, servers
from .services.workers import matchmaking_worker, server_reaper
from .state import close_redis, init_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    mm_task = asyncio.create_task(matchmaking_worker())
    reaper_task = asyncio.create_task(server_reaper())

    yield

    mm_task.cancel()
    reaper_task.cancel()
    await close_redis()


app = FastAPI(lifespan=lifespan)

# Дашборд-панель ходит сюда напрямую с отдельного HTML-файла — без CORS
# браузер зарубит все fetch()-запросы ещё на этапе preflight.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(servers.router)
app.include_router(matchmaking.router)
app.include_router(debug.router)
