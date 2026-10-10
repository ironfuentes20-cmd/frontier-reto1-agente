import asyncio
import base64
import json
import os
import re

import httpx

API_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")


class LLMError(Exception):
    pass


def extraer_json(texto: str) -> dict:
    """Saca el primer objeto JSON del texto, aunque el modelo lo envuelva en ```."""
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.M).strip()
    ini = texto.find("{")
    if ini < 0:
        raise LLMError("la respuesta del modelo no trae JSON")
    prof = 0
    en_cad = False
    esc = False
    for i in range(ini, len(texto)):
        c = texto[i]
        if en_cad:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                en_cad = False
            continue
        if c == '"':
            en_cad = True
        elif c == "{":
            prof += 1
        elif c == "}":
            prof -= 1
            if prof == 0:
                return json.loads(texto[ini:i + 1])
    raise LLMError("JSON incompleto en la respuesta del modelo")


class Modelo:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model or os.environ.get("MODEL", "anthropic/claude-sonnet-4.5")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def decidir(self, system: str, usuario: str, png: bytes) -> dict:
        b64 = base64.b64encode(png).decode()
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": usuario},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        ultimo = None
        for intento in range(3):
            try:
                r = await self.client.post(API_URL, json=body, headers=headers)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise LLMError(f"modelo respondió {r.status_code}")
                if r.status_code != 200:
                    raise LLMError(f"modelo respondió {r.status_code}: {r.text[:200]}")
                texto = r.json()["choices"][0]["message"]["content"]
                if isinstance(texto, list):
                    texto = "".join(p.get("text", "") for p in texto)
                return extraer_json(texto)
            except (LLMError, httpx.HTTPError, KeyError, ValueError) as e:
                ultimo = e
                if "401" in str(e) or "403" in str(e):
                    break
                await asyncio.sleep(1.0 + intento)
        raise LLMError(f"no se pudo obtener una decisión: {ultimo}")
