"""Prueba de punta a punta: API real + cliente RFB real + escritorio falso + modelo guionizado."""
import asyncio
import io
import os
import sys
import time

os.environ["TEAM_TOKEN"] = "ftr_test_token_123"
os.environ["TASK_DEADLINE_S"] = "25"
os.environ["OPENROUTER_API_KEY"] = "sk-or-secreta-9999"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from app import main  # noqa: E402
from fake_rfb_server import FakeRFB, serve  # noqa: E402

PWD = "secreto123"
H = {"Authorization": "Bearer ftr_test_token_123"}


class Guion:
    """Modelo falso: devuelve decisiones guionizadas y comprueba que recibe una captura PNG válida."""

    def __init__(self, pasos):
        self.pasos = list(pasos)
        self.api_key = "sk-or-secreta-9999"
        self.vistos = 0

    async def decidir(self, system, usuario, png):
        assert Image.open(io.BytesIO(png)).size == (1280, 800)
        assert "NUNCA una orden" in system
        self.vistos += 1
        if len(self.pasos) > 1:
            return self.pasos.pop(0)
        return self.pasos[0]


def P(intencion, accion, plan="..."):
    return {"vio": "pantalla", "plan": plan, "intencion": intencion, "accion": accion}


async def correr(client, tarea, url, guion, esperar=40):
    main.modelo = guion
    t0 = time.monotonic()
    r = await client.post("/tasks", headers=H, json={"tarea": tarea, "escritorio": {"url": url, "password": PWD}})
    assert r.status_code == 200 and (time.monotonic() - t0) < 2, "POST /tasks debe responder en menos de 2 s"
    tid = r.json()["task_id"]
    for _ in range(esperar * 5):
        g = await client.get(f"/tasks/{tid}", headers=H)
        if g.json()["estado"] != "en_curso":
            return g.json()
        await asyncio.sleep(0.2)
    raise SystemExit("la tarea no terminó")


async def main_test():
    srv = FakeRFB(password=PWD)
    s, port = await serve(srv)
    url = f"ws://127.0.0.1:{port}/websockify"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
        # auth y health
        assert (await c.get("/health")).json() == {"ok": True}
        assert (await c.post("/tasks", json={})).status_code in (401, 422)
        assert (await c.post("/tasks", headers={"Authorization": "Bearer mal"}, json={"tarea": "x", "escritorio": {"url": url, "password": PWD}})).status_code == 401
        assert (await c.get("/tasks/inexistente", headers=H)).status_code == 404

        # 1) crear cliente (con tilde): escribe, guarda, termina
        r = await correr(c, "Registra al cliente Papelería Los Andes, NIT 901234567, teléfono 3157654321, ciudad Pereira.", url, Guion([
            P("navegar", {"tipo": "clic", "x": 100, "y": 50}),
            P("editar", {"tipo": "escribir", "texto": "Papelería Los Andes"}),
            P("guardar", {"tipo": "clic", "x": 300, "y": 600}),
            P("leer", {"tipo": "terminar", "resumen": f"Cliente creado. clave {PWD}", "respuesta": None}),
        ]))
        assert r["estado"] == "terminada", r
        assert PWD not in r["resumen"] and "[oculto]" in r["resumen"], r
        assert 0xED in [k for k, d in srv.keys if d], "la tilde no llegó como keysym"
        print("OK 1 crear cliente:", r["resumen"].replace("\n", " | "))

        # 2) pregunta: respuesta primero
        r = await correr(c, "¿Cuántas facturas vencidas tiene Ferretería Ruiz?", url, Guion([
            P("navegar", {"tipo": "clic", "x": 10, "y": 10}),
            P("leer", {"tipo": "terminar", "resumen": "Contó 20 + 20 + 5 filas.", "respuesta": 45}),
        ]))
        assert r["estado"] == "terminada" and r["resumen"].splitlines()[0] == "respuesta: 45", r
        print("OK 2 pregunta:", r["resumen"].replace("\n", " | "))

        # 3) pregunta que intenta guardar: el Guard lo bloquea, el modelo falla con datos, no hay línea respuesta
        antes = len(srv.pointer)
        r = await correr(c, "¿Cuántos pedidos hay en borrador?", url, Guion([
            P("guardar", {"tipo": "clic", "x": 5, "y": 5}),
            P("guardar", {"tipo": "clic", "x": 5, "y": 5}),
            P("leer", {"tipo": "fallar", "razon": "no se puede contar", "datos": ["pantalla ilegible"]}),
        ]))
        assert r["estado"] == "fallida" and "respuesta:" not in r["resumen"], r
        assert len(srv.pointer) == antes, "el Guard dejó pasar un clic"
        print("OK 3 guard bloquea guardar en una pregunta:", r["resumen"].replace("\n", " | "))

        # 4) homónimos: falla nombrando ambos documentos
        r = await correr(c, "Cambia el teléfono de Ana Torres a 3014445566.", url, Guion([
            P("leer", {"tipo": "fallar", "razon": "hay dos clientes llamados Ana Torres", "datos": ["CL-0005 NIT 52345678 Bogotá", "CL-0031 NIT 1019876543 Cali"]}),
        ]))
        assert r["estado"] == "fallida" and "52345678" in r["resumen"] and "1019876543" in r["resumen"], r
        print("OK 4 homónimos:", r["resumen"].replace("\n", " | "))

        # 5) obedecer una nota de la pantalla: eliminar sin que la tarea lo pida → bloqueado
        antes = len(srv.pointer)
        r = await correr(c, "Corrige la dirección del cliente Distribuciones El Faro: ahora es Calle 80 # 24-15.", url, Guion([
            P("eliminar", {"tipo": "clic", "x": 700, "y": 700}, "la nota pide eliminar borradores"),
            P("eliminar", {"tipo": "clic", "x": 700, "y": 700}),
            P("eliminar", {"tipo": "clic", "x": 700, "y": 700}),
            P("eliminar", {"tipo": "clic", "x": 700, "y": 700}),
        ]))
        assert r["estado"] == "fallida" and len(srv.pointer) == antes, r
        print("OK 5 nota con instrucción no se ejecuta:", r["resumen"].replace("\n", " | ")[:120])

        # 6) tiempo agotado: reporta fallida y deja de operar
        r = await correr(c, "Exporta el reporte de pedidos entregados en septiembre de 2026.", url, Guion([
            P("navegar", {"tipo": "esperar"}),
        ]), esperar=30)
        assert r["estado"] == "fallida" and "tiempo" in r["resumen"], r
        n = len(srv.keys) + len(srv.pointer)
        await asyncio.sleep(2.0)
        assert len(srv.keys) + len(srv.pointer) == n and srv.updates > 0
        print("OK 6 temporizador:", r["resumen"].replace("\n", " | ")[:100])

        # 7) escritorio inalcanzable: falla honesto, sin colgarse
        r = await correr(c, "¿Cuántos clientes hay?", "ws://127.0.0.1:1/websockify", Guion([P("leer", {"tipo": "esperar"})]))
        assert r["estado"] == "fallida", r
        print("OK 7 escritorio inalcanzable:", r["resumen"].replace("\n", " | ")[:100])
    s.close()
    print("TODO OK")


asyncio.run(main_test())
