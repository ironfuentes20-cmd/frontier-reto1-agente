"""Reporter: arma el resumen en código, con plantilla fija y sin secretos."""
import re

OCULTO = "[oculto]"


def limpiar(texto: str, secretos: list[str]) -> str:
    texto = texto or ""
    for s in secretos:
        if s and len(s) >= 3:
            texto = texto.replace(s, OCULTO)
    texto = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer " + OCULTO, texto)
    texto = re.sub(r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|ftr_[A-Za-z0-9_\-]{6,})", OCULTO, texto)
    return texto.strip()


def _valor(v) -> str:
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x).strip() for x in v)
    return str(v).strip()


def terminada(resumen: str, respuesta, secretos: list[str]) -> str:
    """Si hay respuesta, va primero y en una línea 'respuesta: ...' (el primer número cuenta)."""
    partes = []
    if respuesta is not None and _valor(respuesta) != "":
        partes.append("respuesta: " + limpiar(_valor(respuesta), secretos).replace("\n", " "))
    if resumen:
        partes.append(limpiar(resumen, secretos))
    return "\n".join(partes) if partes else "Tarea terminada."


def fallida(razon: str, datos, secretos: list[str], cambios: bool = False) -> str:
    """Nunca lleva línea 'respuesta:'. Nombra los datos que explican el motivo."""
    partes = ["No se completó la tarea." + ("" if cambios else " No se hizo ningún cambio.")]
    if razon:
        partes.append("Motivo: " + limpiar(razon, secretos))
    d = _valor(datos) if datos else ""
    if d:
        partes.append("Datos: " + limpiar(d, secretos))
    return "\n".join(partes)
