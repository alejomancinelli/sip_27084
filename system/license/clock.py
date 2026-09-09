"""
Estado de reloj en disco: detecta que alguien atrasó la fecha del equipo.

Módulo de infraestructura: sólo el logger. Sin Qt, sin ConfigManager, sin red.

El problema que resuelve es viejo y no tiene solución perfecta sin internet: un
vencimiento contra el reloj del sistema se esquiva atrasando el reloj del sistema. Lo que
sí se puede es que **atrasarlo se note**. Se guarda el instante más alto que se vio, con
un HMAC derivado de la huella de la máquina para que editar el archivo a mano no alcance,
y si el reloj aparece más atrás que ese valor la licencia queda sospechosa.

Lo que esto **no** da: un usuario con root, tiempo y ganas puede borrar el archivo y
volver a empezar. Lo que sí: un atraso casual —o un intento ingenuo— deja de funcionar, y
el equipo lo dice en el log en vez de seguir como si nada. Con internet, la fuente de
tiempo confiable sería el servidor; en planta no la hay.

`time.monotonic()` cubre la otra mitad y vive en el `manager`: sirve para medir cuánto
lleva corriendo este proceso, no se puede atrasar, y se reinicia con el equipo. Por eso
van los dos y no uno.

Que el archivo falte no es un error: es la primera corrida. Que esté y no valide el HMAC,
sí — y se trata igual que un atraso, porque llegar ahí significa que alguien lo tocó.
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

from system.logger import logger

# Un cliente NTP corrige unos segundos hacia atrás cada tanto y eso no es un ataque.
ROLLBACK_TOLERANCE_S = 300.0

_STATE_SEPARATOR = "."


def has_rolled_back(now_utc: datetime, last_seen_utc: datetime | None) -> bool:
    """True si el reloj actual quedó más atrás que el último instante visto."""
    if last_seen_utc is None:
        return False
    return (last_seen_utc - now_utc).total_seconds() > ROLLBACK_TOLERANCE_S


class ClockGuard:
    """
    Lee y escribe el último instante visto, firmado con la clave de la máquina.

    No decide nada: quien compara es el `manager`, que es el que sabe qué hacer con el
    resultado. Ningún método levanta excepción — un estado que no se puede leer se
    reporta como ausente y uno que no se puede escribir se loguea y sigue.
    """

    def __init__(self, state_path: str, machine_key: bytes):
        self._state_path = state_path
        self._machine_key = machine_key

    @property
    def state_path(self) -> str:
        """Dónde vive el estado. Lo pide quien tiene que decir cómo se recupera un retroceso."""
        return self._state_path

    def read_last_seen_utc(self) -> datetime | None:
        """Devuelve el instante guardado, o None si no hay estado válido."""
        try:
            with open(self._state_path, "r", encoding="ascii") as handle:
                stored = handle.read().strip()
        except OSError:
            return None

        # El separador es el ÚLTIMO punto, no el primero: el payload es un JSON con un
        # instante ISO adentro, y los microsegundos de `datetime.now()` traen su propio
        # punto. El HMAC es hexadecimal y no tiene ninguno, así que partir por el último
        # es lo único que separa bien las dos mitades.
        payload, _, signature = stored.rpartition(_STATE_SEPARATOR)
        if not payload or not signature:
            logger.warning("El estado de licencia está incompleto. Se trata como ausente.")
            return None

        if not hmac.compare_digest(signature, self._sign(payload)):
            logger.warning("El estado de licencia no valida: alguien editó el archivo.")
            return None

        try:
            parsed = json.loads(payload)
            return datetime.fromisoformat(str(parsed["last_seen_utc"])).astimezone(timezone.utc)
        except (ValueError, KeyError, TypeError):
            logger.warning("El estado de licencia tiene una fecha ilegible.")
            return None

    def save_last_seen_utc(self, now_utc: datetime) -> bool:
        """Guarda el instante como último visto. Devuelve False si no se pudo escribir."""
        payload = json.dumps({"last_seen_utc": now_utc.astimezone(timezone.utc).isoformat()},
                             sort_keys=True, separators=(",", ":"))
        content = f"{payload}{_STATE_SEPARATOR}{self._sign(payload)}"

        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            with open(self._state_path, "w", encoding="ascii") as handle:
                handle.write(content)
        except OSError as e:
            logger.warning(f"No se pudo guardar el estado de licencia: {e}.")
            return False
        return True

    def _sign(self, payload: str) -> str:
        return hmac.new(self._machine_key, payload.encode("ascii"), hashlib.sha256).hexdigest()
