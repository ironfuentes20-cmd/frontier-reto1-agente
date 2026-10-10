"""Estado de las tareas: en memoria y, si hay REDIS_URL, también en Redis (sobrevive reinicios)."""
import json
import os
import time


class Store:
    def __init__(self):
        self.mem: dict[str, dict] = {}
        self.redis = None
        url = os.environ.get("REDIS_URL")
        if url:
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(url, decode_responses=True)

    async def set(self, task_id: str, estado: str, resumen: str = "") -> None:
        rec = {"estado": estado, "resumen": resumen, "t": time.time()}
        self.mem[task_id] = rec
        if self.redis:
            try:
                await self.redis.set(f"task:{task_id}", json.dumps(rec), ex=86400)
            except Exception:
                pass  # el estado en memoria sigue valiendo

    async def get(self, task_id: str) -> dict | None:
        if task_id in self.mem:
            return self.mem[task_id]
        if self.redis:
            try:
                raw = await self.redis.get(f"task:{task_id}")
                if raw:
                    return json.loads(raw)
            except Exception:
                return None
        return None
