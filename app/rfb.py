"""Cliente RFB (VNC) propio sobre WebSocket (websockify).

Se escribió a mano en lugar de usar noVNC en un navegador porque noVNC perdió las
tildes y la ñ al teclear. Aquí cada carácter se envía como un keysym directo.
Solo usa codificación Raw: es lenta pero sencilla y nunca falla.
"""
import asyncio
import io
import struct

import numpy as np
import websockets
from Crypto.Cipher import DES
from PIL import Image

SPECIAL_KEYS = {
    "enter": 0xFF0D, "return": 0xFF0D, "backspace": 0xFF08, "tab": 0xFF09,
    "escape": 0xFF1B, "esc": 0xFF1B, "delete": 0xFFFF, "suprimir": 0xFFFF,
    "home": 0xFF50, "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
    "pageup": 0xFF55, "pagedown": 0xFF56, "end": 0xFF57,
    "shift": 0xFFE1, "ctrl": 0xFFE3, "control": 0xFFE3, "alt": 0xFFE9,
    "space": 0x20, "f5": 0xFFC2,
}


class RFBError(Exception):
    pass


def keysym(ch: str) -> int:
    """Keysym X11 de un carácter. Latin-1 usa su propio código; el resto, 0x01000000 + Unicode."""
    if ch == "\n":
        return 0xFF0D
    if ch == "\t":
        return 0xFF09
    o = ord(ch)
    if 0x20 <= o <= 0x7E or 0xA0 <= o <= 0xFF:
        return o
    return 0x01000000 + o


def _vnc_key(password: str) -> bytes:
    """La autenticación VNC usa DES con los bits de cada byte de la clave invertidos."""
    pw = password.encode("latin-1", "ignore")[:8].ljust(8, b"\0")
    return bytes(int(f"{b:08b}"[::-1], 2) for b in pw)


class RFBClient:
    def __init__(self, url: str, password: str):
        self.url = url
        self.password = password
        self.ws = None
        self.buf = bytearray()
        self.width = 0
        self.height = 0
        self.fb = None  # (alto, ancho, 4) BGRX
        self.mx = 0
        self.my = 0
        self.closed = False
        self._pump_task = None
        self._pump_error = None
        self._last_update = 0.0
        self._updates = 0

    # ---- bajo nivel -------------------------------------------------
    async def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            msg = await self.ws.recv()
            if isinstance(msg, str):
                msg = msg.encode("latin-1")
            self.buf.extend(msg)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    async def _send(self, data: bytes) -> None:
        await self.ws.send(data)

    # ---- conexión ---------------------------------------------------
    async def connect(self, timeout: float = 15.0) -> None:
        try:
            self.ws = await websockets.connect(
                self.url, subprotocols=["binary"], max_size=None,
                ping_interval=None, open_timeout=timeout, compression=None,
            )
        except Exception:
            self.ws = await websockets.connect(
                self.url, max_size=None, ping_interval=None,
                open_timeout=timeout, compression=None,
            )
        await asyncio.wait_for(self._handshake(), timeout)
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """Único lector del socket: mantiene el framebuffer al día con todo lo que el escritorio envíe."""
        try:
            while True:
                if await self._read_message() == "update":
                    self._last_update = asyncio.get_running_loop().time()
                    self._updates += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._pump_error = e

    async def _handshake(self) -> None:
        server_ver = await self._read(12)
        if not server_ver.startswith(b"RFB "):
            raise RFBError("el servidor no habla RFB")
        old = server_ver.startswith(b"RFB 003.003")
        await self._send(b"RFB 003.003\n" if old else b"RFB 003.008\n")

        if old:
            choice = struct.unpack(">I", await self._read(4))[0]
        else:
            n = (await self._read(1))[0]
            if n == 0:
                ln = struct.unpack(">I", await self._read(4))[0]
                raise RFBError("el servidor rechazó la conexión: " + (await self._read(ln)).decode("utf-8", "ignore"))
            types = list(await self._read(n))
            choice = 2 if 2 in types else (1 if 1 in types else None)
            if choice is None:
                raise RFBError(f"tipos de seguridad no soportados: {types}")
            await self._send(bytes([choice]))
        if choice == 2:
            challenge = await self._read(16)
            await self._send(DES.new(_vnc_key(self.password), DES.MODE_ECB).encrypt(challenge))
        if choice != 1 or not old:
            result = struct.unpack(">I", await self._read(4))[0]
            if result != 0:
                raise RFBError("autenticación VNC rechazada")
        await self._send(b"\x01")  # escritorio compartido
        self.width, self.height = struct.unpack(">HH", await self._read(4))
        await self._read(16)  # formato de píxel del servidor, lo reemplazamos abajo
        name_len = struct.unpack(">I", await self._read(4))[0]
        await self._read(name_len)
        self.fb = np.zeros((self.height, self.width, 4), np.uint8)

        # 32 bpp, color verdadero, little-endian: los bytes llegan como B, G, R, X
        pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await self._send(b"\x00\x00\x00\x00" + pf)
        await self._send(struct.pack(">BxH", 2, 2) + struct.pack(">ii", 0, -223))  # Raw + DesktopSize

    async def close(self) -> None:
        self.closed = True
        try:
            if self._pump_task:
                self._pump_task.cancel()
            if self.ws:
                await self.ws.close()
        except Exception:
            pass

    # ---- lectura de pantalla ---------------------------------------
    async def _read_message(self) -> str:
        t = (await self._read(1))[0]
        if t == 0:
            _, n = struct.unpack(">BH", await self._read(3))
            for _ in range(n):
                x, y, w, h, enc = struct.unpack(">HHHHi", await self._read(12))
                if enc == 0:
                    data = await self._read(w * h * 4)
                    self.fb[y:y + h, x:x + w] = np.frombuffer(data, np.uint8).reshape(h, w, 4)
                elif enc == -223:
                    self.width, self.height = w, h
                    self.fb = np.zeros((h, w, 4), np.uint8)
                else:
                    raise RFBError(f"codificación no soportada: {enc}")
            return "update"
        if t == 1:
            _, _first, n = struct.unpack(">BHH", await self._read(5))
            await self._read(n * 6)
        elif t == 2:
            pass
        elif t == 3:
            await self._read(3)
            ln = struct.unpack(">I", await self._read(4))[0]
            await self._read(ln)
        else:
            raise RFBError(f"mensaje del servidor desconocido: {t}")
        return "other"

    async def screenshot(self, timeout: float = 15.0, quiet: float = 0.35, max_wait: float = 2.5) -> bytes:
        """Pide la pantalla completa, espera a que el escritorio termine de repintar y la devuelve como PNG."""
        loop = asyncio.get_running_loop()
        antes = self._updates
        await self._send(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))
        t0 = loop.time()
        while self._updates == antes:  # al menos una actualización posterior a la petición
            if self._pump_error:
                raise self._pump_error
            if loop.time() - t0 > timeout:
                raise asyncio.TimeoutError("el escritorio no envió la pantalla")
            await asyncio.sleep(0.02)
        # la pantalla puede seguir repintándose (texto recién escrito): esperar a que se calme
        while loop.time() - self._last_update < quiet and loop.time() - t0 < max_wait:
            await asyncio.sleep(0.05)
        if self._pump_error:
            raise self._pump_error
        rgb = self.fb[:, :, [2, 1, 0]]
        out = io.BytesIO()
        Image.fromarray(rgb, "RGB").save(out, "PNG")
        return out.getvalue()

    # ---- entrada ----------------------------------------------------
    async def _key(self, sym: int, down: bool) -> None:
        await self._send(struct.pack(">BBxxI", 4, 1 if down else 0, sym))

    async def _pointer(self, mask: int) -> None:
        await self._send(struct.pack(">BBHH", 5, mask, self.mx, self.my))

    def _clamp(self, x, y):
        self.mx = max(0, min(int(x), self.width - 1))
        self.my = max(0, min(int(y), self.height - 1))

    async def move(self, x, y) -> None:
        self._clamp(x, y)
        await self._pointer(0)

    async def click(self, x, y, button: int = 1, double: bool = False) -> None:
        """Un clic de menos de 100 ms entre bajar y soltar se ignora: aquí dura 150 ms."""
        self._clamp(x, y)
        await self._pointer(0)
        await asyncio.sleep(0.05)
        mask = {1: 1, 2: 2, 3: 4}.get(button, 1)
        for _ in range(2 if double else 1):
            await self._pointer(mask)
            await asyncio.sleep(0.15)
            await self._pointer(0)
            await asyncio.sleep(0.08)

    async def scroll(self, x, y, dy: int) -> None:
        self._clamp(x, y)
        await self._pointer(0)
        mask = 8 if dy < 0 else 16  # botón 4 = arriba, botón 5 = abajo
        for _ in range(max(1, min(abs(int(dy)), 20))):
            await self._pointer(mask)
            await asyncio.sleep(0.03)
            await self._pointer(0)
            await asyncio.sleep(0.03)

    async def type_text(self, text: str, delay: float = 0.03) -> None:
        for ch in text:
            sym = keysym(ch)
            await self._key(sym, True)
            await asyncio.sleep(0.02)
            await self._key(sym, False)
            await asyncio.sleep(delay)

    async def press(self, combo: str, repeat: int = 1) -> None:
        """Teclas como 'End', 'BackSpace' o combinaciones 'ctrl+a'."""
        syms = []
        for part in combo.split("+"):
            p = part.strip()
            if p.lower() in SPECIAL_KEYS:
                syms.append(SPECIAL_KEYS[p.lower()])
            elif len(p) == 1:
                syms.append(keysym(p))
            else:
                raise RFBError(f"tecla desconocida: {p}")
        for _ in range(max(1, min(int(repeat), 60))):
            for s in syms:
                await self._key(s, True)
                await asyncio.sleep(0.02)
            for s in reversed(syms):
                await self._key(s, False)
            await asyncio.sleep(0.04)
