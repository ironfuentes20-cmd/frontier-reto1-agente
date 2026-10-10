"""Bucle del agente: observar → decidir → (Guard) → actuar → verificar, hasta terminar o agotar el tiempo."""
import asyncio
import hashlib
import time

from . import reporter
from .guard import Guard
from .llm import LLMError, Modelo
from .prompts import SYSTEM, mensaje_usuario
from .rfb import RFBClient, RFBError

TIEMPO_MAX = float(__import__("os").environ.get("TASK_DEADLINE_S", "270"))  # 4:30 de los 5 minutos
MAX_PASOS = int(__import__("os").environ.get("MAX_STEPS", "60"))


def _num(v, lo, hi):
    n = int(float(v))
    if not lo <= n <= hi:
        raise ValueError(f"coordenada fuera de pantalla: {n}")
    return n


async def _actuar(rfb: RFBClient, a: dict) -> str:
    t = a.get("tipo")
    if t in ("clic", "doble_clic"):
        x, y = _num(a["x"], 0, rfb.width - 1), _num(a["y"], 0, rfb.height - 1)
        await rfb.click(x, y, double=(t == "doble_clic"))
        return f"{'doble ' if t == 'doble_clic' else ''}clic en ({x},{y})"
    if t == "escribir":
        texto = str(a.get("texto", ""))
        await rfb.type_text(texto)
        return f"escribió «{texto[:60]}»"
    if t == "tecla":
        teclas = str(a.get("teclas", ""))
        rep = int(a.get("repetir", 1) or 1)
        await rfb.press(teclas, rep)
        return f"tecla {teclas}" + (f" ×{rep}" if rep > 1 else "")
    if t == "scroll":
        x, y = _num(a.get("x", 640), 0, rfb.width - 1), _num(a.get("y", 400), 0, rfb.height - 1)
        dy = int(a.get("dy", 3))
        await rfb.scroll(x, y, dy)
        return f"scroll {dy:+d} en ({x},{y})"
    if t == "esperar":
        await asyncio.sleep(1.5)
        return "esperó"
    raise ValueError(f"acción desconocida: {t!r}")


async def ejecutar(tarea: str, url: str, password: str, modelo: Modelo, secretos: list[str], log=print) -> tuple[str, str]:
    """Devuelve (estado, resumen). Nunca lanza: todo error se convierte en 'fallida' honesta."""
    secretos = [s for s in secretos if s]
    guard = Guard(tarea)
    historial: list[str] = []
    rfb = RFBClient(url, password)
    inicio = time.monotonic()

    def info(msg: str):
        log(f"[{time.monotonic() - inicio:5.1f}s] {reporter.limpiar(msg, secretos)}")

    async def bucle() -> tuple[str, str]:
        aviso = ""
        notas = ""
        prev_hash = None
        ultima_fue_accion = False
        ult_clic = None
        mismo_clic = 0
        seguidos = 0
        sin_guardar = 0
        sin_respuesta = 0
        info(f"tarea ({guard.tipo}): {tarea}")
        for paso in range(1, MAX_PASOS + 1):
            png = await rfb.screenshot()
            h = hashlib.md5(png).hexdigest()
            if ultima_fue_accion and h == prev_hash:
                aviso = ("La captura es idéntica a la anterior. Puede que tu última acción no hiciera efecto o que la pantalla "
                         "aún no haya repintado. No saques conclusiones todavía: comprueba el indicador de la pantalla (por ejemplo "
                         "«Página N») y si el botón que pulsaste sigue activo, reinténtalo UNA vez de otra forma. Solo asume que "
                         "no hay más resultados si el botón «Siguiente» aparece desactivado (gris).")
            prev_hash = h
            d = await modelo.decidir(SYSTEM, mensaje_usuario(tarea, historial, aviso, notas), png)
            aviso = ""
            if d.get("notas"):
                notas = str(d["notas"])[:600]
            a = d.get("accion") or {}
            intencion = str(d.get("intencion") or "navegar")
            info(f"paso {paso}: [{intencion}] {d.get('plan', '')} → {a}")

            ok, motivo = guard.revisar(intencion, a)
            if not ok:
                seguidos += 1
                historial.append(f"BLOQUEADO [{intencion}]: {motivo}")
                aviso = f"Tu última acción fue bloqueada: {motivo}."
                info(f"guard: {motivo}")
                if seguidos >= 4:
                    return "fallida", reporter.fallida(
                        "la tarea exigía acciones que el agente no puede hacer de forma segura", [motivo],
                        secretos, cambios=guard.guardados > 0 or guard.exportados > 0)
                continue
            seguidos = 0
            ultima_fue_accion = a.get("tipo") not in ("terminar", "fallar")

            t = a.get("tipo")
            if t == "fallar":
                return "fallida", reporter.fallida(str(a.get("razon", "")), a.get("datos"), secretos,
                                                   cambios=guard.guardados > 0 or guard.exportados > 0)
            if t == "terminar":
                resp = a.get("respuesta")
                if guard.tipo == "pregunta" and (resp is None or str(resp).strip() == "") and sin_respuesta < 2:
                    sin_respuesta += 1
                    aviso = "Una pregunta necesita el valor en 'respuesta'. Si no se puede responder, usa 'fallar'."
                    continue
                if guard.tipo == "modificacion" and guard.guardados == 0 and sin_guardar < 2:
                    sin_guardar += 1
                    aviso = ("Todavía no has pulsado Guardar ni confirmado ningún cambio. Si la tarea pide un cambio, "
                             "hazlo y verifícalo; si no se puede, usa 'fallar'.")
                    continue
                if guard.tipo != "pregunta":
                    resp = None
                return "terminada", reporter.terminada(str(a.get("resumen", "")), resp, secretos)

            if a.get("tipo") in ("clic", "doble_clic"):
                try:
                    pt = (int(float(a["x"])), int(float(a["y"])))
                except (KeyError, TypeError, ValueError):
                    pt = None
                if pt and ult_clic and abs(pt[0] - ult_clic[0]) <= 4 and abs(pt[1] - ult_clic[1]) <= 4:
                    mismo_clic += 1
                else:
                    mismo_clic = 1 if pt else 0
                ult_clic = pt
                if mismo_clic > 3:
                    seguidos += 1
                    historial.append(f"BLOQUEADO: ya hiciste clic {mismo_clic - 1} veces en {pt} sin que nada avanzara")
                    aviso = ("Ese mismo clic ya no sirve: llevas varios en el mismo punto. Si es un desplegable, enfócalo con un "
                             "clic y elige la opción con el teclado (escribe su primera letra o usa la flecha Abajo y Enter). "
                             "Si es un botón que no responde, busca otra ruta; si no la hay, usa 'fallar' explicando qué no respondió.")
                    info(f"guard: clic repetido en {pt}")
                    if seguidos >= 4:
                        return "fallida", reporter.fallida(
                            f"un elemento de la pantalla en {pt} no respondió a varios clics y no encontré otra ruta", None,
                            secretos, cambios=guard.guardados > 0 or guard.exportados > 0)
                    continue
            else:
                ult_clic, mismo_clic = None, 0
            try:
                desc = await _actuar(rfb, a)
            except (ValueError, KeyError, TypeError, RFBError) as e:
                historial.append(f"ERROR al ejecutar {a}: {e}")
                aviso = f"No pude ejecutar tu acción ({e}). Revisa el formato."
                continue
            guard.registrar(intencion)
            historial.append(f"[{intencion}] {desc}")
            if len(historial) >= 3 and len({h for h in historial[-3:]}) == 1:
                aviso = ("Repetiste la MISMA acción 3 veces y la pantalla no avanzó. Ese clic no funciona: "
                         "prueba otro elemento, otro punto del botón, la tecla Enter o una ruta distinta del menú.")
                historial.append("(repetición detectada: cambia de estrategia)")
            if intencion == "guardar":
                historial.append("(guardado hecho: no repitas Guardar; verifica el resultado en pantalla)")
            await asyncio.sleep(1.0)  # la pantalla tarda hasta 1 s en repintar
        return "fallida", reporter.fallida(
            "se alcanzó el máximo de pasos sin poder completar la tarea", None, secretos,
            cambios=guard.guardados > 0 or guard.exportados > 0)

    try:
        await rfb.connect()
        return await asyncio.wait_for(bucle(), timeout=TIEMPO_MAX)
    except asyncio.TimeoutError:
        ultimo = "; ".join(historial[-3:])
        return "fallida", reporter.fallida(
            f"se agotó el tiempo ({int(TIEMPO_MAX)} s) antes de poder verificar el resultado. Últimas acciones: {ultimo}",
            None, secretos, cambios=guard.guardados > 0 or guard.exportados > 0)
    except LLMError as e:
        return "fallida", reporter.fallida(f"falló el modelo de visión: {e}", None, secretos,
                                           cambios=guard.guardados > 0 or guard.exportados > 0)
    except (RFBError, OSError, asyncio.TimeoutError, Exception) as e:  # noqa: B014
        return "fallida", reporter.fallida(f"no se pudo operar el escritorio: {type(e).__name__}: {e}", None, secretos,
                                           cambios=guard.guardados > 0 or guard.exportados > 0)
    finally:
        await rfb.close()  # tras reportar no se hace ni un clic más
