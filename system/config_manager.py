"""
Acceso único al config.yaml de la aplicación.

Módulo de infraestructura: solo importa el logger. Expone un singleton
thread-safe que lee el archivo una vez, entrega valores por ruta punteada
(`get("cameras.camera_1.name")`) y persiste cambios con `set()` + `save()`.

Si el archivo falta, no se puede leer o no es un mapa YAML, el módulo no levanta
excepción: arma una configuración de rescate en memoria y lo avisa por
`is_using_fallback`. Con la config de rescate no hay cámaras configuradas, así
que quien publique estado hacia afuera puede distinguir ese caso de una parada
normal.

Dos puntos del contrato que no se ven en las firmas:
  - `get` devuelve copias de los dict y las list: quien los recibe los puede
    recorrer sin lock y no puede modificar la config sin pasar por `set`.
  - `save()` reescribe el archivo entero desde el diccionario en memoria; los
    comentarios del YAML original no sobreviven.
"""

import copy
import os
import shutil
import threading

import yaml

from system.logger import logger
from system.paths import DATA_DIR

_CONFIG_FILENAME = "config.yaml"
_BACKUP_SUFFIX = ".bak"   # copia de la versión anterior, una sola
_TMP_SUFFIX = ".tmp"

# Config de rescate: lo mínimo para que la app arranque y pueda loguear. Las
# claves espejan las de config.yaml, si no el fallback no serviría para leerlo.
# Arranca en DEBUG a propósito: si la config no se pudo leer, hace falta ver todo.
_FALLBACK_CONFIG = {
    "system": {"log_level": "DEBUG", "paths": {"logs": "./data/logs"}},
    "cameras": {},
}


def _default_config_path() -> str:
    """
    `config.yaml` en la raíz de la instalación, sin depender del CWD del proceso.

    Sale de `DATA_DIR` y no del `__file__` de este archivo: compilado, este módulo vive
    adentro de la distribución —de sólo lectura y reemplazada al actualizar—, y el config
    es justamente lo que la app escribe y lo que no se puede perder.
    """
    return os.path.join(DATA_DIR, _CONFIG_FILENAME)


class ConfigManager:
    """
    Singleton thread-safe que centraliza la lectura, modificación y guardado del
    config.yaml.

    Se instancia una vez en el arranque de la app; el resto de los módulos hace
    `ConfigManager()` y recibe la misma instancia. El `config_path` del primer
    llamado es el que queda: los siguientes lo ignoran y lo avisan.

    Todas las operaciones toman el mismo RLock, así que `get` / `set` / `load` /
    `save` se pueden llamar desde cualquier hilo.
    """

    _instance = None
    _lock = threading.RLock()

    def __new__(cls, config_path: str | None = None) -> "ConfigManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init(config_path)
            elif config_path and config_path != cls._instance._config_path:
                logger.warning(
                    f"ConfigManager ya inicializado con {cls._instance._config_path}; "
                    f"se ignora {config_path}."
                )
            return cls._instance

    def _init(self, config_path: str | None):
        self._config_path = config_path or _default_config_path()
        self._config_data: dict = {}
        self._is_using_fallback = False
        # Claves ya avisadas: el aviso de clave ausente sale una sola vez, porque
        # hay `get` que corren por frame y llenarían el log.
        self._missing_keys: set[str] = set()
        self.load()

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def config_path(self) -> str:
        """
        Qué archivo se leyó y cuál va a reescribir `save()`.

        Es para mostrarlo, no para abrirlo: quien necesite leer o escribir el config lo
        hace por `get` y `set`. Existe porque desde que el programa y la instalación son
        dos raíces distintas, «el config» ya no es un lugar único, y una herramienta que
        avisa que va a cambiar una medición tiene que poder decir en qué archivo.
        """
        return self._config_path

    @property
    def is_using_fallback(self) -> bool:
        """True si el archivo no se pudo usar y la config en memoria es la de rescate."""
        return self._is_using_fallback

    def load(self) -> bool:
        """
        Lee el archivo y reemplaza la config en memoria. Sirve para recargar.

        Un load exitoso limpia el estado de rescate. Devuelve False y deja la
        config de rescate si el archivo falta, no se puede leer, está vacío o no
        es un mapa de claves.
        """
        with self._lock:
            parsed = self._read_file()
            if parsed is None:
                self._apply_fallback()
                return False

            self._config_data = parsed
            self._is_using_fallback = False
            self._missing_keys.clear()
            logger.debug(f"{_CONFIG_FILENAME} cargado en memoria desde {self._config_path}")
            return True

    def get(self, key_path: str, default: object = None) -> object:
        """
        Devuelve el valor de una ruta punteada ('cameras.camera_1.roi.x_px').

        Si la ruta no existe devuelve `default`. Los dict y las list se devuelven
        copiados, así el llamador los recorre sin lock y sin tocar la config.
        """
        with self._lock:
            if not key_path:
                logger.warning(f"Ruta de config vacía. Se usa el default: {default}")
                return default

            node = self._config_data
            for key in key_path.split("."):
                if not isinstance(node, dict) or key not in node:
                    self._warn_missing(key_path, default)
                    return default
                node = node[key]

            if isinstance(node, (dict, list)):
                return copy.deepcopy(node)
            return node

    def set(self, key_path: str, value: object) -> bool:
        """
        Escribe un valor en una ruta punteada, creando los niveles que falten.

        No toca el disco: eso lo hace `save()`. Devuelve False sin modificar nada
        si un nivel intermedio de la ruta ya existe y no es un mapa.
        """
        with self._lock:
            if not key_path:
                logger.error("Ruta de config vacía; no se escribe nada.")
                return False

            keys = key_path.split(".")
            node = self._config_data
            for i, key in enumerate(keys[:-1]):
                child = node.get(key)
                if child is None:
                    child = {}
                    node[key] = child
                elif not isinstance(child, dict):
                    prefix = ".".join(keys[:i + 1])
                    logger.error(
                        f"Ruta de config inválida: '{prefix}' no es un mapa; "
                        f"no se escribe '{key_path}'."
                    )
                    return False
                node = child

            # Copia por el mismo motivo que `get`: la config no comparte objetos
            # con nadie de afuera.
            node[keys[-1]] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
            logger.debug(f"Config: '{key_path}' = {value}")
            return True

    def save(self) -> bool:
        """
        Escribe el diccionario en memoria al archivo, de forma atómica.

        Deja un .tmp al lado, lo sincroniza a disco y recién entonces lo mueve
        sobre el original: un corte de energía deja el archivo viejo entero, nunca
        uno a medio escribir. Los comentarios del YAML original no sobreviven.

        Antes de pisarlo guarda una copia en `.bak`, que es **la versión anterior, no un
        histórico**. Es la red contra el modo de falla propio de este archivo: acá no se
        escribe un valor, se reescribe el archivo entero desde lo que haya en memoria, así
        que un campo que no se llenó bien se persiste como si fuera un valor elegido. Eso
        no rompe nada —una escala en cero publica ceros creíbles— y para cuando se nota
        puede haber pasado un turno.

        No guarda —y devuelve False— si la config en memoria es la de rescate y el
        archivo existe: sería pisar con un esqueleto un archivo que todavía se
        puede arreglar a mano. Tampoco guarda si algún valor no es YAML plano; el
        archivo queda como estaba.
        """
        with self._lock:
            if self._is_using_fallback and os.path.exists(self._config_path):
                logger.error(
                    f"No se guarda: la config en memoria es la de rescate y "
                    f"{self._config_path} existe. Se conserva el archivo original."
                )
                return False

            self._write_backup()
            tmp_path = f"{self._config_path}{_TMP_SUFFIX}"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    # safe_dump y no dump: el dumper completo escribe tags
                    # !!python/... que después safe_load rechaza. Mejor que falle
                    # el save que dejar un archivo que la app no puede releer.
                    yaml.safe_dump(self._config_data, f, default_flow_style=False,
                                   sort_keys=False, allow_unicode=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._config_path)
                logger.info(f"{_CONFIG_FILENAME} guardado en {self._config_path}")
                return True
            except (OSError, yaml.YAMLError) as e:
                logger.error(f"Error escribiendo {self._config_path}: {e}")
                self._discard_tmp(tmp_path)
                return False

    def _write_backup(self):
        """
        Copia el archivo actual a `.bak` antes de pisarlo. Un fallo no impide guardar.

        Se copia y no se renombra: renombrar dejaría al proceso sin config si la escritura
        siguiente falla. Y si la copia no se puede hacer se avisa y se sigue: perder el
        respaldo es peor que no guardar, pero no tanto como no poder guardar.
        """
        if not os.path.exists(self._config_path):
            return
        backup_path = f"{self._config_path}{_BACKUP_SUFFIX}"
        try:
            shutil.copy2(self._config_path, backup_path)
        except OSError as e:
            logger.warning(f"No se pudo dejar el respaldo en {backup_path}: {e}")

    # ── Internos ─────────────────────────────────────────────────────────────

    def _read_file(self) -> dict | None:
        """Lee y parsea el archivo. Devuelve None si no se puede usar."""
        if not os.path.exists(self._config_path):
            logger.error(f"Falla crítica: {self._config_path} no existe.")
            return None

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                parsed = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logger.error(f"Falla crítica leyendo {self._config_path}: {e}")
            return None

        if parsed is None:
            logger.error(f"Falla crítica: {self._config_path} está vacío.")
            return None
        if not isinstance(parsed, dict):
            logger.error(
                f"Falla crítica: {self._config_path} no es un mapa de claves "
                f"(se leyó un {type(parsed).__name__})."
            )
            return None
        return parsed

    def _apply_fallback(self):
        """Deja en memoria la config de rescate."""
        logger.warning("Se usa la configuración de rescate (fallback).")
        self._is_using_fallback = True
        self._config_data = copy.deepcopy(_FALLBACK_CONFIG)
        self._missing_keys.clear()

    def _warn_missing(self, key_path: str, default: object):
        if key_path in self._missing_keys:
            return
        self._missing_keys.add(key_path)
        logger.warning(f"Clave de config ausente: '{key_path}'. Se usa el default: {default}")

    def _discard_tmp(self, tmp_path: str):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
