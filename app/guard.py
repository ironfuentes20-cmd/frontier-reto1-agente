"""Guard determinista: decide en código, no en el modelo, qué acciones se permiten.

La tarea del usuario es la única fuente de permiso. Un texto que aparezca en la
pantalla (una nota, un aviso) nunca autoriza nada.
"""
import re
import unicodedata
from dataclasses import dataclass, field

def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


# Primera palabra de una consulta (sin tildes). Una tarea que empieza así no cambia nada.
_INICIO_CONSULTA = {
    "cuanto", "cuantos", "cuanta", "cuantas", "cual", "cuales", "que", "quien", "quienes", "donde", "cuando",
    "como", "hay", "tiene", "tienen", "existe", "esta", "estan", "dime", "indica", "consulta", "cuenta",
    "revisa", "verifica", "dame", "muestra", "lista", "averigua", "busca", "encuentra", "mira", "cuentame",
}
_EXPORTAR = re.compile(r"\bexport\w*|\breporte\b|\binforme\b|\bdescarg\w*", re.I)
# Verbos de acción en forma de orden o infinitivo, como palabra completa ("borrador" no cuenta como "borra")
_ELIMINAR = re.compile(r"\b(elimin(a|ar|e|en)|borr(a|ar|e|en)|suprim(e|ir|an)|quit(a|ar|e|en))\b", re.I)
_ANULAR = re.compile(r"\b(anul(a|ar|e|en)|cancel(a|ar|e|en))\b", re.I)
_REVERTIR = re.compile(r"\b(revier?t(e|a|ir|an)|deshac(e|er)|deshaz(a|as))\b", re.I)
_CAMBIO = re.compile(r"\b(registr(a|ar|e|en)|cre(a|ar|e|en)|agreg(a|ar|ue|uen)|a[nñ]ad(e|ir|a|an)|cambi(a|ar|e|en)|"
                     r"corrig(e|ir|en)|actualiz(a|ar|e|en)|modific(a|ar|que|quen)|edit(a|ar|e|en)|ingres(a|ar|e|en)|"
                     r"asign(a|ar|e|en)|confirm(a|ar|e|en)|marc(a|ar|que|quen)|pon(e|er|le|erle)|guard(a|ar|e|en)|carg(a|ar|ue|uen))\b",
                     re.I)


def clasificar(tarea: str) -> str:
    """'pregunta' | 'exportacion' | 'modificacion'. Ante la duda, la lectura más restrictiva que encaje."""
    limpia = _sin_tildes(tarea)
    primera = re.sub(r"[^a-z]", "", (re.findall(r"[a-zA-Z\u00c0-\u017f]+", limpia) or [""])[0])
    if primera in _INICIO_CONSULTA:
        return "pregunta"
    if _EXPORTAR.search(limpia):
        return "exportacion"
    if _CAMBIO.search(limpia) or _ELIMINAR.search(limpia) or _ANULAR.search(limpia) or _REVERTIR.search(limpia):
        return "modificacion"
    if "?" in tarea:
        return "pregunta"
    return "modificacion"


INTENCIONES = {"navegar", "leer", "editar", "guardar", "exportar", "eliminar", "anular", "revertir"}


@dataclass
class Guard:
    tarea: str
    tipo: str = field(init=False)
    guardados: int = 0
    exportados: int = 0
    bloqueos: int = 0

    def __post_init__(self):
        self.tipo = clasificar(self.tarea)

    def revisar(self, intencion: str, accion: dict) -> tuple[bool, str]:
        """Devuelve (permitida, motivo si no)."""
        t = accion.get("tipo")
        if t in ("terminar", "fallar"):
            return True, ""
        intencion = (intencion or "navegar").lower()
        if intencion not in INTENCIONES:
            return False, f"intención desconocida '{intencion}'; usa una de {sorted(INTENCIONES)}"

        if intencion in ("editar", "guardar"):
            if self.tipo != "modificacion":
                return False, "la tarea no pide modificar datos: solo puedes navegar y leer"
            if intencion == "guardar" and self.guardados >= 3:
                return False, "ya guardaste 3 veces; verifica el resultado en pantalla en vez de guardar otra vez"
        if intencion == "exportar":
            if not _EXPORTAR.search(_sin_tildes(self.tarea)):
                return False, "la tarea no pide exportar nada"
            if self.exportados >= 1:
                return False, "ya exportaste una vez; cada exportación deja un registro permanente"
        limpia = _sin_tildes(self.tarea)
        if intencion == "eliminar" and (self.tipo != "modificacion" or not _ELIMINAR.search(limpia)):
            return False, "la tarea no pide eliminar nada"
        if intencion == "anular" and (self.tipo != "modificacion" or not _ANULAR.search(limpia)):
            return False, "la tarea no pide anular nada"
        if intencion == "revertir" and (self.tipo != "modificacion" or not _REVERTIR.search(limpia)):
            return False, "la tarea no pide revertir nada"
        return True, ""

    def registrar(self, intencion: str) -> None:
        intencion = (intencion or "").lower()
        if intencion == "guardar":
            self.guardados += 1
        elif intencion == "exportar":
            self.exportados += 1
