"""
La fachada del subsistema de licencia: carga el archivo, lo valida y dice qué habilita.

Es lo único que importa `main.py`. Los demás módulos de `system/license/` son internos
del subsistema: el formato, la firma, la huella y el estado de reloj no salen de acá.

**Nada de esto sirve sin compilar.** Corriendo desde fuentes, sacar la validación es
borrar un `if`, así que el estado es `unlicensed_build` y no se enforcea nada — con una
línea de log al arrancar, para que nunca sea silencioso. La marca la pone Nuitka
(`__compiled__`), no una variable de entorno ni una clave de config: un `LICENSE_DEV=1`
es un string en el binario y es lo primero que se busca.

Dónde vive cada archivo:

    license.lic               en la raíz del repo, al lado del config.yaml
    data/license_state.bin    el último instante visto, firmado con la huella

Las dos rutas salen de `system/paths.py` y no del directorio desde el que se lanzó el
proceso, por el motivo que ese módulo ya explica: un servicio arranca en
`C:\\Windows\\system32`. En un build standalone de Nuitka esa raíz es la carpeta del
ejecutable, que es donde también queda el `config.yaml`.

**Sin licencia utilizable, el equipo abre igual, en modo de puesta en marcha.** `absent`
e `invalid` ya no frenan el arranque: en vez de eso son los dos estados que MÁS
restringen. Sin nada que diga qué se compró, ningún pipeline arranca
(`has_feature()` da `False` para cualquier nombre) y ninguna medición sale
(`is_publishing_allowed()` da `False`), sin importar `policy.DEFAULT_POLICY`. Las cámaras
siguen capturando —el feed en vivo es lo que permite verificar el cableado durante la
puesta en marcha— y la interfaz completa sigue disponible, empezando por la pestaña de
licencia: sin eso, un equipo entregado sin licencia no tendría cómo generar la solicitud
ni instalar el archivo que vuelve, porque no hay un intérprete de Python a mano en un
binario compilado.

Con una licencia que sí parsea —vencida, de otra máquina, con el reloj atrasado— hay un
payload firmado y su `policy` decide qué tan tolerante ser: ahí sí se sabe qué se vendió,
así que sus entitlements se aplican tal como la licencia los declara.

El orden en que se diagnostica importa y no es alfabético: primero si hay archivo, después
si la firma cierra, después si es de este equipo, y **el retroceso de reloj antes que el
vencimiento** — con la fecha movida, preguntar si venció no significa nada.
"""

import hashlib
import os
import shutil
import sys
import time
from datetime import datetime, timezone

from system import paths
from system.config_manager import ConfigManager
from system.license import clock, fingerprint, policy, public_key, schema, verify
from system.logger import logger

STATE_VALID = "valid"                        # todo cierra
STATE_ABSENT = "absent"                      # no hay archivo de licencia
STATE_INVALID = "invalid"                    # firma, formato o clave desconocida
STATE_FOREIGN = "foreign"                    # no es de este equipo, proyecto o modelo
STATE_EXPIRED = "expired"                    # venció
STATE_TAMPERED = "tampered"                  # el reloj del equipo se atrasó
STATE_UNLICENSED_BUILD = "unlicensed_build"  # corriendo desde fuentes: no se enforcea

#: Estados en los que el equipo está operando con una licencia que no vale.
INVALID_STATES = (STATE_ABSENT, STATE_INVALID, STATE_FOREIGN, STATE_EXPIRED, STATE_TAMPERED)

#: Estados sin licencia utilizable: no hay archivo, o el que hay no se puede verificar.
#: No hay payload firmado del que leer una política, así que la decisión no es de la
#: licencia sino del build, y es la más restrictiva que hay: `has_feature()` niega
#: cualquier nombre e `is_publishing_allowed()` da `False`, sin mirar
#: `policy.DEFAULT_POLICY` para nada de esto. Lo que NO restringen es la cámara
#: (`allowed_camera_slots()` sigue permisivo: el feed en vivo es lo que sirve durante la
#: puesta en marcha) ni la interfaz, que es donde se instala la licencia que saca al
#: equipo de este estado.
NO_LICENSE_STATES = (STATE_ABSENT, STATE_INVALID)

DESCRIPTIONS = {
    STATE_VALID:             "Licencia válida",
    STATE_ABSENT:            "Sin archivo de licencia",
    STATE_INVALID:           "Licencia inválida",
    STATE_FOREIGN:           "Licencia de otro equipo",
    STATE_EXPIRED:           "Licencia vencida",
    STATE_TAMPERED:          "Reloj del equipo atrasado",
    STATE_UNLICENSED_BUILD:  "Ejecución desde fuentes",
}

#: Feature que declara un pipeline que no es un addon. Ausente en el config = éste.
DEFAULT_FEATURE = "core"

# TODO: decisión pendiente — cada cuánto revalida el proceso en caliente. Una hora alcanza
# para que congelar el reloj y no reiniciar nunca deje de ser una estrategia; bajarlo no
# cuesta nada porque el re-chequeo no vuelve a leer el disco.
RECHECK_INTERVAL_MS = 3_600_000

# Días antes del vencimiento en los que ya conviene avisar, para que la renovación no sea
# una urgencia el día que la línea se para.
EXPIRY_WARNING_DAYS = 30

#: Lo que publica el registro de días restantes cuando la licencia es perpetua.
PERPETUAL_DAYS_SENTINEL = 9999

_LICENSE_FILENAME = "license.lic"
_STATE_FILENAME = "data/license_state.bin"
_HASH_CHUNK_BYTES = 1 << 20   # 1 MiB: hashear un .engine de cientos de MB de a poco

# La marca del build, y no hay otra: ni entorno, ni config, ni argumento de línea de
# comandos. Un `LICENSE_DEV=1` es un string en el binario y es lo primero que se busca.
#
# Se miran las dos señales que deja Nuitka porque fallan para el lado opuesto: si este
# módulo quedara fuera de la compilación, `__compiled__` no estaría y el binario correría
# sin enforcear nada sin que se note. En un standalone las dos dan True.
IS_COMPILED = "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def get_license_path() -> str:
    """Ruta del archivo de licencia; la raíz del repo es la única ancla fija."""
    return os.path.join(paths.PROJECT_ROOT, _LICENSE_FILENAME)


def get_state_path() -> str:
    """Ruta del estado de reloj."""
    return paths.resolve(None, _STATE_FILENAME)


def read_feature(declared: object) -> str:
    """
    Nombre del feature que exige un pipeline, tal como sale del config.

    **Un pipeline declara UN feature.** Dos propósitos independientes —medir un proceso y
    vigilar una zona— son dos pipelines: son dos hilos, se venden por separado, y
    juntarlos haría que un cliente que no compró el addon pierda también la medición que
    sí pagó.

    Vacío o ausente es `core`, que es el pipeline normal.

    Una lista es un error de config y se avisa: el pipeline queda bloqueado —el nombre no
    coincide con ninguno de la licencia— y sin este aviso quedaría bloqueado en silencio,
    que es la peor combinación posible.
    """
    if declared is None:
        return DEFAULT_FEATURE

    if not isinstance(declared, str):
        logger.warning(
            f"[Licencia] El `feature` de un pipeline es un nombre suelto y llegó "
            f"{type(declared).__name__}: {declared!r}. Ese pipeline no va a arrancar "
            f"hasta que se corrija el config."
        )
        return str(declared)

    return declared.strip() or DEFAULT_FEATURE


class LicenseManager:
    """
    Estado de la licencia de este equipo, y qué habilita.

    Se construye una vez en el arranque y se consulta desde el hilo de la GUI: el
    veredicto se calcula en el constructor y en `recheck()`, no en cada property.
    """

    def __init__(self, config: ConfigManager, *, license_path: str | None = None,
                 state_path: str | None = None, now_provider=None):
        self._config = config
        self._license_path = license_path or get_license_path()
        self._now_provider = now_provider or _utc_now
        self._components = fingerprint.read_components()
        self._clock_guard = clock.ClockGuard(
            state_path or get_state_path(), fingerprint.build_machine_key(self._components)
        )
        self._started_monotonic_s = time.monotonic()

        self._license: schema.License | None = None
        self._state = STATE_ABSENT
        self._reason = ""
        self._policy = policy.DEFAULT_POLICY
        self._fingerprint_matches = 0

        self._evaluate()
        self._log_state()

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def policy(self) -> str:
        return self._policy

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def is_valid(self) -> bool:
        return self._state in (STATE_VALID, STATE_UNLICENSED_BUILD)

    @property
    def license_id(self) -> str:
        return self._license.license_id if self._license else ""

    @property
    def max_cameras(self) -> int | None:
        """Cupo de cámaras, o None si no hay licencia que lo declare."""
        return self._license.max_cameras if self._license else None

    @property
    def features(self) -> tuple:
        return self._license.features if self._license else ()

    @property
    def days_remaining(self) -> int | None:
        """
        Días hasta el vencimiento; None si es perpetua, si no hay licencia, o si el
        reloj no es confiable.

        Una licencia ya vencida devuelve 0, no un negativo: lo que el PLC necesita saber
        es cuánto queda, y de una vencida no queda nada.

        Con el reloj atrasado devuelve None y no un número: la cuenta saldría de una
        fecha que el propio subsistema acaba de declarar no confiable, y publicar «faltan
        400 días» porque alguien movió el reloj es peor que no publicar nada.
        """
        if self._license is None or self._license.expires_at is None:
            return None
        if self._state == STATE_TAMPERED:
            return None
        remaining = (self._license.expires_at - self._now_provider()).total_seconds()
        return max(0, int(remaining // 86400))

    @property
    def is_expiring_soon(self) -> bool:
        days = self.days_remaining
        return days is not None and days <= EXPIRY_WARNING_DAYS

    # ── Lo que habilita ──────────────────────────────────────────────────────

    def allowed_camera_slots(self, camera_slots) -> tuple:
        """
        Filtra los slots declarados al cupo de la licencia, respetando el orden recibido.

        El corte es por orden de declaración y es determinista a propósito: que la cámara
        que se apaga cambie entre arranques sería peor que el límite mismo.
        """
        slots = tuple(camera_slots)
        limit = self.max_cameras
        if limit is None or not self._enforces_entitlements():
            return slots
        return slots[:limit]

    def has_feature(self, feature: str | None) -> bool:
        """
        True si la licencia habilita ese addon. Un feature por pipeline.

        Corriendo desde fuentes (`unlicensed_build`) es siempre True: no hay nada que
        enforcear. **Sin licencia utilizable (`absent`/`invalid`) es siempre False**: no
        hay nada que diga qué se compró, así que ningún pipeline arranca. Antes esos dos
        estados frenaban el arranque entero; ahora, en vez de abortar el proceso, son los
        que más restringen dentro de él.

        Con una licencia que sí parsea —vencida, de otro equipo, con el reloj atrasado—
        hay un payload firmado y sus features mandan como siempre. Espera el nombre ya
        leído del config con `read_feature()`, que es quien avisa si viene con una forma
        que no corresponde; cualquier cosa que no sea uno de los nombres de la licencia
        devuelve False, porque ante la duda no se habilita.
        """
        if self._state == STATE_UNLICENSED_BUILD:
            return True
        if self._state in NO_LICENSE_STATES:
            return False
        if not self._enforces_entitlements():
            return True
        return str(feature or DEFAULT_FEATURE).strip() in self._license.features

    def is_publishing_allowed(self) -> bool:
        """
        True si las mediciones pueden salir hacia el PLC y hacia la telemetría.

        Sin licencia utilizable esto es siempre False, sin mirar la política: no hay
        payload firmado que la declare, así que no hay nada tolerante que consultar. Con
        una licencia que sí parsea, manda su `policy` como siempre.
        """
        if self.is_valid:
            return True
        if self._state in NO_LICENSE_STATES:
            return False
        return not policy.should_block_publishing(self._policy)

    def should_report_invalid(self) -> bool:
        """True si el estado tiene que verse en el bit, el registro y la UI."""
        return self._state in INVALID_STATES and policy.should_report_invalid(self._policy)

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def recheck(self) -> bool:
        """
        Revalida vencimiento, reloj y huella. Devuelve True si el estado cambió.

        Con una licencia ya cargada no vuelve a leer el disco: lo que cambia mientras el
        proceso corre es la fecha, no la firma. Sin licencia sí mira de nuevo, y así
        dejar el archivo en su lugar alcanza para que el equipo lo tome sin reiniciar.
        """
        previous_state = self._state
        previous_policy = self._policy

        if self._license is None:
            self._evaluate()
        else:
            self._evaluate_parsed(self._license)

        if self._state != previous_state or self._policy != previous_policy:
            self._log_state()
            return True
        return False

    def install(self, source_path: str) -> tuple:
        """
        Verifica un archivo de licencia y, si vale, lo deja instalado. Devuelve `(ok, texto)`.

        Se verifica **antes** de copiar: una renovación fallida no puede dejar al equipo
        peor que antes, así que el archivo que ya estaba no se toca hasta que el nuevo
        pasó la firma y la huella.
        """
        try:
            with open(source_path, "r", encoding="ascii") as handle:
                token = handle.read()
        except OSError as e:
            return False, f"No se pudo leer {source_path}: {e}"

        try:
            candidate = schema.parse_payload(verify.verify_token(token))
        except (schema.LicenseFormatError, verify.LicenseSignatureError) as e:
            return False, str(e)

        if not fingerprint.is_match(self._components, candidate.fingerprint,
                                    candidate.min_matches):
            return False, ("La licencia no corresponde a este equipo: coinciden "
                           f"{fingerprint.count_matches(self._components, candidate.fingerprint)} "
                           f"de {candidate.min_matches} fuentes exigidas.")

        try:
            os.makedirs(os.path.dirname(self._license_path) or ".", exist_ok=True)
            shutil.copyfile(source_path, self._license_path)
        except OSError as e:
            return False, f"No se pudo escribir la licencia: {e}"

        # La emisión es una fecha firmada por el proveedor, así que sube la marca de reloj
        # aunque este equipo nunca la haya visto pasar: un equipo cuya fecha ya estaba
        # atrasada antes de instalar queda marcado ahora y no dentro de un año.
        self._clock_guard.advance_to(candidate.issued_at)

        self._evaluate()
        self._log_state()
        # Cámaras y pipelines se deciden una sola vez, al construir la aplicación: instalar
        # una licencia no los reinstancia. Sin este aviso, alguien podría instalar la
        # licencia correcta y seguir viendo el mismo modo restringido, sin saber por qué.
        return True, (f"Licencia {candidate.license_id} instalada. Reiniciar la aplicación "
                      "para que tome el cupo de cámaras y los features habilitados.")

    def get_status(self) -> dict:
        """
        Estado para la UI, el log y el cableado. Claves estables:

            state                 uno de los STATE_* de este módulo
            policy                uno de los POLICY_* de policy.py
            reason                por qué, en texto, o cadena vacía
            license_id            identificador de la emisión
            client, project_id    a quién y a qué instalación corresponde
            issued_at             fecha de emisión ISO, o cadena vacía
            expires_at            fecha de vencimiento ISO; vacía si es perpetua
            days_remaining        int, o None si es perpetua o no hay licencia
            is_perpetual          bool
            fingerprint_matches   cuántas fuentes coinciden
            fingerprint_required  cuántas exige la licencia
            fingerprint_sources   fuentes leídas en este equipo, ordenadas
            max_cameras           cupo, o None
            features              tupla de addons habilitados
            is_compiled           si este build enforcea
            uptime_s              segundos de reloj monótono desde el arranque
            should_report_invalid si el estado tiene que verse en el bit, el registro,
                                   el chip del footer y el cartel de arranque
        """
        return {
            "state": self._state,
            "policy": self._policy,
            "reason": self._reason,
            "should_report_invalid": self.should_report_invalid(),
            "license_id": self.license_id,
            "client": self._license.client if self._license else "",
            "project_id": self._license.project_id if self._license else "",
            "issued_at": _format_instant(self._license.issued_at if self._license else None),
            "expires_at": _format_instant(self._license.expires_at if self._license else None),
            "days_remaining": self.days_remaining,
            "is_perpetual": bool(self._license and self._license.is_perpetual),
            "fingerprint_matches": self._fingerprint_matches,
            "fingerprint_required": self._license.min_matches if self._license else 0,
            "fingerprint_sources": tuple(sorted(self._components)),
            "max_cameras": self.max_cameras,
            "features": self.features,
            "is_compiled": IS_COMPILED,
            "uptime_s": round(time.monotonic() - self._started_monotonic_s, 1),
        }

    # ── Diagnóstico ──────────────────────────────────────────────────────────

    def _evaluate(self):
        self._license = None
        self._fingerprint_matches = 0

        if not IS_COMPILED:
            self._set_state(STATE_UNLICENSED_BUILD, policy.POLICY_OFF,
                            "Ejecución desde fuentes: la licencia no se enforcea.")
            return

        if not os.path.isfile(self._license_path):
            self._set_state(STATE_ABSENT, policy.DEFAULT_POLICY,
                            f"No hay archivo de licencia en {self._license_path}.")
            return

        try:
            with open(self._license_path, "r", encoding="ascii") as handle:
                token = handle.read()
        except OSError as e:
            self._set_state(STATE_INVALID, policy.DEFAULT_POLICY,
                            f"No se pudo leer la licencia: {e}")
            return

        try:
            parsed = schema.parse_payload(verify.verify_token(token))
        except (schema.LicenseFormatError, verify.LicenseSignatureError) as e:
            self._set_state(STATE_INVALID, policy.DEFAULT_POLICY, str(e))
            return

        self._license = parsed
        self._evaluate_parsed(parsed)

    def _evaluate_parsed(self, parsed: schema.License):
        """Revalida lo que cambia sin volver a leer el disco: huella, reloj y vencimiento."""
        self._fingerprint_matches = fingerprint.count_matches(self._components,
                                                              parsed.fingerprint)
        now = self._now_provider()

        if not fingerprint.is_match(self._components, parsed.fingerprint, parsed.min_matches):
            self._set_state(STATE_FOREIGN, parsed.policy,
                            f"La licencia es de otro equipo: coinciden "
                            f"{self._fingerprint_matches} de {parsed.min_matches} fuentes.")
            return

        mismatch = self._find_installation_mismatch(parsed)
        if mismatch:
            self._set_state(STATE_FOREIGN, parsed.policy, mismatch)
            return

        mismatch = self._find_model_mismatch(parsed)
        if mismatch:
            self._set_state(STATE_FOREIGN, parsed.policy, mismatch)
            return

        # Antes que el vencimiento: con la fecha movida, preguntar si venció no significa nada.
        if clock.has_rolled_back(now, self._clock_guard.read_last_seen_utc()):
            # La ruta va en el motivo porque la recuperación es borrar ese archivo, y
            # quien atiende el teléfono no tiene por qué saberla de memoria. No hay
            # comando para hacerlo: en un binario compilado no existe la CLI, y el estado
            # ya es borrable por cualquiera con acceso al equipo — el esquema nunca
            # pretendió otra cosa.
            self._set_state(STATE_TAMPERED, parsed.policy,
                            "El reloj del equipo quedó más atrás que el último instante "
                            "conocido: el arranque anterior, o la emisión de la licencia "
                            "instalada. La licencia no se puede evaluar. Si la fecha del "
                            "equipo es la correcta, borrar "
                            f"{self._clock_guard.state_path} y volver a arrancar.")
            return

        self._clock_guard.save_last_seen_utc(now)

        if parsed.expires_at is not None and now > parsed.expires_at:
            self._set_state(STATE_EXPIRED, parsed.policy,
                            f"La licencia venció el {_format_instant(parsed.expires_at)}.")
            return

        self._set_state(STATE_VALID, parsed.policy, "")

    def _find_installation_mismatch(self, parsed: schema.License) -> str:
        """
        Texto del desacuerdo con `project:` del config, o cadena vacía.

        No es un control de seguridad —los dos valores salen de un archivo que el cliente
        edita— sino la forma de que mandar el archivo equivocado se note el primer día.
        """
        expected_client = str(self._config.get("project.client", "") or "")
        expected_project = str(self._config.get("project.project_id", "") or "")

        if parsed.project_id and expected_project and parsed.project_id != expected_project:
            return (f"La licencia es del proyecto '{parsed.project_id}' y este equipo es "
                    f"'{expected_project}'.")
        if parsed.client and expected_client and parsed.client != expected_client:
            return (f"La licencia es del cliente '{parsed.client}' y este equipo es "
                    f"'{expected_client}'.")
        return ""

    def _find_model_mismatch(self, parsed: schema.License) -> str:
        """Texto del desacuerdo con los pesos instalados, o cadena vacía."""
        for model_slot, expected_digest in parsed.model_hashes.items():
            model_path = str(self._config.get(f"inference.models.{model_slot}.path", "") or "")
            if not model_path:
                return f"La licencia habilita '{model_slot}', que este equipo no tiene configurado."

            digest = _hash_file(paths.resolve(model_path, model_path))
            if not digest:
                return f"No se pudo leer los pesos de '{model_slot}' para verificarlos."
            if digest != expected_digest:
                return f"Los pesos de '{model_slot}' no son los que habilita la licencia."
        return ""

    def _enforces_entitlements(self) -> bool:
        return policy.should_enforce_entitlements(self._policy)

    def _set_state(self, state: str, applied_policy: str, reason: str):
        self._state = state
        self._policy = policy.normalize(applied_policy)
        self._reason = reason

    def _log_state(self):
        detail = f" {self._reason}" if self._reason else ""
        summary = (f"[Licencia] {DESCRIPTIONS.get(self._state, self._state)} "
                   f"(política: {self._policy}).{detail}")

        if self._state == STATE_VALID:
            logger.info(summary)
            if self.is_expiring_soon:
                days = self.days_remaining
                logger.warning(f"[Licencia] Vence en {days} "
                               f"{'día' if days == 1 else 'días'}. "
                               f"Conviene renovarla antes de que pare la línea.")
        elif self._state == STATE_UNLICENSED_BUILD:
            logger.info(summary)
        else:
            logger.warning(summary)

        if not public_key.has_any_key() and IS_COMPILED:
            logger.error("[Licencia] Este build no trae ninguna clave pública: ninguna "
                         "licencia puede validar. La tabla la genera el repositorio de "
                         "firma al compilar; ver docs/licensing.md.")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_instant(instant: datetime | None) -> str:
    return instant.isoformat() if instant else ""


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()
