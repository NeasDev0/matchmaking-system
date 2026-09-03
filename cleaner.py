import redis
import asyncio
import asyncpg

r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
r.flushdb()
print("очищен редис")

async def main():
    conn = await asyncpg.connect("postgresql://user:password@localhost:5432/userdb")
    await conn.execute("DROP TABLE IF EXISTS users;")
    await conn.close()
    print("Таблица users удалена — пересоздастся при следующем запуске authservice")

asyncio.run(main())

