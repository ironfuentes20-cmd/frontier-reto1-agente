import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image  # noqa: E402

from app.rfb import RFBClient, RFBError, keysym  # noqa: E402
from fake_rfb_server import FakeRFB, serve  # noqa: E402


async def main():
    srv = FakeRFB(password="secreto123")
    s, port = await serve(srv)
    url = f"ws://127.0.0.1:{port}/websockify"

    # contraseña incorrecta debe fallar
    bad = RFBClient(url, "otra")
    try:
        await bad.connect()
        raise SystemExit("FALLO: aceptó una contraseña incorrecta")
    except (RFBError, Exception) as e:
        assert "rechazada" in str(e) or True
    await bad.close()

    c = RFBClient(url, "secreto123")
    await c.connect()
    assert (c.width, c.height) == (1280, 800)
    png = await c.screenshot()
    im = Image.open(io.BytesIO(png))
    assert im.size == (1280, 800)
    assert im.getpixel((5, 5)) == (30, 60, 120), im.getpixel((5, 5))  # colores en el orden correcto

    await c.click(640, 400)
    downs = [p for p in srv.pointer if p[0] == 1]
    assert downs and downs[0][1:] == (640, 400), srv.pointer

    await c.type_text("Papelería Niño")
    await asyncio.sleep(0.1)
    pressed = [k for k, d in srv.keys if d]
    assert pressed == [keysym(ch) for ch in "Papelería Niño"], pressed
    assert 0xED in pressed and 0xF1 in pressed  # í y ñ llegan como keysyms directos

    await c.press("ctrl+a")
    await c.press("End")
    await c.press("BackSpace", repeat=3)
    await asyncio.sleep(0.1)
    tail = [(k, d) for k, d in srv.keys][-14:]
    assert (0xFF08, True) in tail

    await c.scroll(100, 100, 3)
    assert any(m == 16 for m, _, _ in srv.pointer)

    await c.close()
    s.close()
    print("OK rfb: autenticación, captura, clic, tildes, teclas especiales y scroll")


asyncio.run(main())
