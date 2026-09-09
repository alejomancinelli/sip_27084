"""
Qué hace el programa cuando la licencia no vale.

Módulo puro: sin Qt, sin I/O, sin ConfigManager. Es una sola decisión y tiene archivo
propio porque es la que todavía no está tomada: cambiarla es editar `DEFAULT_POLICY` acá
y nada más, y mientras tanto cada licencia emitida puede traer la suya en el payload.

**La política viaja firmada dentro de la licencia.** No está en `config.yaml` —que el
cliente edita— ni compilada como único valor —que obligaría a un build por cada decisión
comercial—. El default de este archivo manda sólo cuando no hay licencia que leer.

Las tres, y qué significan de verdad:

    off       Nada se enforcea. Es lo que aplica corriendo desde fuentes, donde la
              validación no tiene sentido porque sacarla es borrar un `if`.
    warn      Se aplican los entitlements —cupo de cámaras, features— y el estado se
              publica y se loguea, pero las mediciones siguen saliendo.
    degrade   Además, no se publica ninguna medición: ni al PLC ni a la telemetría. Lo
              que **no** se corta es el canal — el heartbeat, la salud del equipo y las
              palabras de estado se siguen publicando. Bajar el Modbus dejaría al PLC
              viendo un enlace muerto, que es indistinguible de un cable cortado, y el
              integrador saldría a buscar el problema equivocado.

Los entitlements se aplican también con `warn`. «Warn» es sobre la licencia inválida
—vencida, de otra máquina, ausente—, no sobre el cupo: una licencia válida de cuatro
cámaras no habilita cinco por más suave que sea la política.
"""

POLICY_OFF = "off"
POLICY_WARN = "warn"
POLICY_DEGRADE = "degrade"

#: Vocabulario completo. Cualquier otro valor en una licencia cae en el default.
POLICIES = (POLICY_OFF, POLICY_WARN, POLICY_DEGRADE)

# TODO: decisión pendiente — qué hace un equipo entregado cuando la licencia no vale.
# Queda en `warn` hasta que se defina: avisa por todos lados y no frena la línea.
DEFAULT_POLICY = POLICY_WARN

DESCRIPTIONS = {
    POLICY_OFF:     "Sin enforcement",
    POLICY_WARN:    "Avisa, sin cortar mediciones",
    POLICY_DEGRADE: "Avisa y deja de publicar mediciones",
}


def normalize(policy: str | None) -> str:
    """Devuelve una política del vocabulario; cualquier otra cosa cae en `DEFAULT_POLICY`."""
    candidate = str(policy or "").strip().lower()
    return candidate if candidate in POLICIES else DEFAULT_POLICY


def should_enforce_entitlements(policy: str | None) -> bool:
    """True si el cupo de cámaras y los features se aplican."""
    return normalize(policy) != POLICY_OFF


def should_block_publishing(policy: str | None) -> bool:
    """True si las mediciones no salen hacia el PLC ni hacia la telemetría."""
    return normalize(policy) == POLICY_DEGRADE


def should_report_invalid(policy: str | None) -> bool:
    """True si el estado inválido se publica —bit, registro, chip— y se loguea."""
    return normalize(policy) != POLICY_OFF
