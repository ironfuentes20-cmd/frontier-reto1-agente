"""Servidor RFB falso sobre WebSocket para probar el cliente sin un escritorio real.

Entiende lo mismo que el cliente (handshake 3.8, autenticación VNC, Raw), así que
detecta errores de código, no de interpretación del protocolo: eso se prueba
contra el escritorio real.
"""
import asyncio
import struct

import numpy as np
import websockets
from Crypto.Cipher import DES
from PIL import Image, ImageDraw

W, H = 1280, 800


def _vnc_key(password: str) -> bytes:
    pw = password.encode("latin-1")[:8].ljust(8, b"\0")
    return bytes(int(f"{b:08b}"[::-1], 2) for b in pw)


class FakeRFB:
    def __init__(self, password="secreto123", auth=True):
        self.password = password
        self.auth = auth
        self.keys = []      # (keysym, down)
        self.pointer = []   # (mask, x, y)
        self.updates = 0
        self.frame = self._draw("SIGECOM de prueba")

    def _draw(self, text):
        im = Image.new("RGB", (W, H), (30, 60, 120))
        ImageDraw.Draw(im).text((100, 100), text, fill=(255, 255, 255))
        a = np.array(im, dtype=np.uint8)
        bgrx = np.zeros((H, W, 4), np.uint8)
        bgrx[:, :, 0], bgrx[:, :, 1], bgrx[:, :, 2] = a[:, :, 2], a[:, :, 1], a[:, :, 0]
        return bgrx

    async def handler(self, ws):
        buf = bytearray()

        async def rd(n):
            while len(buf) < n:
                m = await ws.recv()
                buf.extend(m if isinstance(m, bytes) else m.encode())
            out = bytes(buf[:n])
            del buf[:n]
            return out

        await ws.send(b"RFB 003.008\n")
        assert (await rd(12)).startswith(b"RFB 003.008")
        await ws.send(bytes([1, 2 if self.auth else 1]))
        choice = (await rd(1))[0]
        if choice == 2:
            ch = bytes(range(16))
            await ws.send(ch)
            resp = await rd(16)
            ok = resp == DES.new(_vnc_key(self.password), DES.MODE_ECB).encrypt(ch)
            await ws.send(struct.pack(">I", 0 if ok else 1))
            if not ok:
                return
        else:
            await ws.send(struct.pack(">I", 0))
        await rd(1)  # ClientInit
        name = b"fake"
        await ws.send(struct.pack(">HH", W, H) + struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
                      + struct.pack(">I", len(name)) + name)
        try:
            while True:
                t = (await rd(1))[0]
                if t == 0:
                    await rd(19)
                elif t == 2:
                    _, n = struct.unpack(">BH", await rd(3))
                    await rd(4 * n)
                elif t == 3:
                    inc, x, y, w, h = struct.unpack(">BHHHH", await rd(9))
                    self.updates += 1
                    sub = self.frame[y:y + h, x:x + w]
                    await ws.send(b"\x00\x00" + struct.pack(">H", 1) + struct.pack(">HHHHi", x, y, w, h, 0) + sub.tobytes())
                elif t == 4:
                    down, _pad, sym = struct.unpack(">BHI", await rd(7))
                    self.keys.append((sym, bool(down)))
                elif t == 5:
                    mask, x, y = struct.unpack(">BHH", await rd(5))
                    self.pointer.append((mask, x, y))
                else:
                    raise RuntimeError(f"mensaje de cliente desconocido {t}")
        except websockets.ConnectionClosed:
            return


async def serve(server: FakeRFB, port=0):
    s = await websockets.serve(server.handler, "127.0.0.1", port, subprotocols=["binary"], max_size=None)
    return s, s.sockets[0].getsockname()[1]
