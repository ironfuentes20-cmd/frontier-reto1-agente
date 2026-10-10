# Agente SIGECOM · Reto 1 (Hackathon Frontier Bogotá)

Un agente que opera aplicaciones sin API **solo mirando la pantalla**: recibe una tarea en español y un escritorio remoto (VNC), la ejecuta paso a paso y reporta `terminada` o `fallida` con un resumen verificable.

## Cómo funciona

```
POST /tasks ──► API (responde task_id en < 2 s) ──► Cola y estado (memoria o Redis)
                                                        │
                                              Worker (una tarea, tope 270 s)
                    observar ─► decidir (modelo de visión) ─► Guard ─► actuar ─► verificar
                        ▲                                                         │
                        └─────────────────────────────────────────────────────────┘
                    cliente RFB propio ◄──► escritorio remoto (wss://…/websockify)
                                      Reporter ─► resumen por plantilla, sin secretos
```

| Archivo | Qué hace |
| --- | --- |
| `app/rfb.py` | Cliente RFB/VNC sobre WebSocket. Envía cada carácter como *keysym* directo (tildes y ñ funcionan), clic de 150 ms, scroll y teclas especiales. |
| `app/agent.py` | Bucle observar → decidir → actuar → verificar, con temporizador y cierre honesto. Tras reportar no hace ninguna acción más. |
| `app/guard.py` | Decide **en código** qué acciones se permiten: una pregunta no puede guardar; Exportar, Eliminar, Anular y Revertir solo si la tarea lo pide; máximo una exportación. |
| `app/reporter.py` | Resumen por plantilla: `respuesta:` primero y sin secretos; en fallos, nombra los datos que explican el motivo. |
| `app/llm.py`, `app/prompts.py` | Modelo de visión por OpenRouter y sus instrucciones. |
| `app/main.py` | API: `GET /health`, `POST /tasks`, `GET /tasks/{task_id}`; todas con `Authorization: Bearer <token del equipo>`. |

## Decisiones clave

- **Texto en pantalla = dato, nunca orden.** Una nota dentro de una ficha no autoriza nada.
- **Ambigüedad = no tocar.** Con más de un candidato la tarea termina `fallida` y el resumen nombra a todos.
- **Cierre honesto.** Antes de reportar éxito el agente verifica en pantalla; si vence el tiempo reporta `fallida` con lo que sí consta.

## Variables de entorno

| Variable | Para qué |
| --- | --- |
| `TEAM_TOKEN` | Token del equipo que deben traer todas las llamadas. |
| `OPENROUTER_API_KEY` | Key de OpenRouter (secreto: solo en el servidor, nunca en el repo). |
| `MODEL` | Modelo con visión de OpenRouter (por defecto `anthropic/claude-sonnet-4.5`). |
| `REDIS_URL` | Opcional. Si existe, el estado de las tareas sobrevive a reinicios. |
| `TASK_DEADLINE_S` | Tope por tarea en segundos (por defecto 270). |
| `MAX_CONCURRENT` | Tareas simultáneas (por defecto 4). |

## Pruebas

```bash
pip install -r requirements.txt
python tests/test_rfb.py   # cliente RFB contra un servidor falso
python tests/test_e2e.py   # API + agente + Guard + Reporter con modelo guionizado
```

## Ejemplo

```bash
curl -s -X POST "$URL/tasks" -H "Authorization: Bearer $TEAM_TOKEN" -H "Content-Type: application/json" \
  -d '{"tarea":"¿Cuántas facturas vencidas tiene Ferretería Ruiz?","escritorio":{"url":"wss://…/websockify","password":"…"}}'
curl -s "$URL/tasks/<task_id>" -H "Authorization: Bearer $TEAM_TOKEN"
```
