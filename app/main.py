import asyncio
import hmac
import os
import uuid

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import agent, reporter
from .llm import Modelo
from .store import Store

TEAM_TOKEN = os.environ.get("TEAM_TOKEN", "")
MAX_CONCURRENTES = int(os.environ.get("MAX_CONCURRENT", "4"))

app = FastAPI(title="Agente SIGECOM", docs_url=None, redoc_url=None, openapi_url=None)
store = Store()
modelo = Modelo()
cupo = asyncio.Semaphore(MAX_CONCURRENTES)
tareas_vivas: set[asyncio.Task] = set()


class Escritorio(BaseModel):
    url: str
    password: str


class NuevaTarea(BaseModel):
    tarea: str
    escritorio: Escritorio


def autorizar(authorization: str | None = Header(default=None)):
    esperado = f"Bearer {TEAM_TOKEN}"
    if not TEAM_TOKEN or not authorization or not hmac.compare_digest(authorization, esperado):
        raise HTTPException(status_code=401, detail="token inválido")


@app.get("/probar", response_class=HTMLResponse)
async def probar():
    return (Path(__file__).parent / "probar.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"ok": True}


async def _correr(task_id: str, nueva: NuevaTarea):
    secretos = [nueva.escritorio.password, TEAM_TOKEN, modelo.api_key]
    try:
        async with cupo:
            estado, resumen = await agent.ejecutar(
                nueva.tarea, nueva.escritorio.url, nueva.escritorio.password, modelo, secretos,
                log=lambda m: print(f"{task_id[:8]} {m}", flush=True))
    except Exception as e:  # nunca dejar una tarea en_curso para siempre
        estado, resumen = "fallida", reporter.fallida(f"error interno: {type(e).__name__}", None, secretos, cambios=True)
    await store.set(task_id, estado, reporter.limpiar(resumen, secretos))


@app.post("/tasks", dependencies=[Depends(autorizar)])
async def crear_tarea(nueva: NuevaTarea):
    task_id = uuid.uuid4().hex
    await store.set(task_id, "en_curso", "")
    t = asyncio.create_task(_correr(task_id, nueva))
    tareas_vivas.add(t)
    t.add_done_callback(tareas_vivas.discard)
    return {"task_id": task_id}


@app.get("/tasks/{task_id}", dependencies=[Depends(autorizar)])
async def estado_tarea(task_id: str):
    rec = await store.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="tarea desconocida")
    return {"estado": rec["estado"], "resumen": rec["resumen"]}
