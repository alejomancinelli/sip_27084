"""
Contrato de los backends de telemetría: el destino donde terminan los datos del
proceso.

Maquinaria genérica: reutilizable entre proyectos. Este módulo no habla ningún
protocolo; define qué tiene que ofrecer un backend y qué recibe.

Ciclo de vida, en este orden y desde un solo hilo:

    setup()          abre la conexión y deja el resultado en `status`
    write(batch)     escribe un lote de registros
    close()          libera todo; idempotente

Cada registro del batch es un dict con estas claves estables:

    measurement : str    nombre de la serie
    tags        : dict   etiquetas de la serie
    fields      : dict   valores medidos
    time        : float  epoch en segundos del instante en que se midió

`time` es el instante de la medición y no el de la escritura. Un backend que lo
ignore y deje sellar al servidor apila todos los registros de un batch en el mismo
instante, y en una base de series temporales eso deja un solo punto por batch.

Configuración: cada backend lee su sección `telemetry.<service_name>` del
config.yaml, donde `enabled` es lo que lo prende. Las credenciales no van al
config —se versiona y se edita desde la UI—: se leen de variables de entorno.

Ningún backend propaga errores: los deja en `status` y en el log. Quien produce
telemetría no puede caerse porque un destino no esté.

Agregar un destino —SQL, otro broker, un archivo— es implementar esta interfaz en
`system/backends/` y sumarlo a la lista de PersistenceThread. Ningún otro archivo
se toca.
"""

from abc import ABC, abstractmethod

from system.config_manager import ConfigManager

# Vocabulario de `status`, para que la UI y los tests no repitan los literales.
STATUS_DISABLED = "disabled"      # apagado por config, o sin su librería instalada
STATUS_CONNECTING = "connecting"  # solo los backends que conectan en segundo plano
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"            # config incompleta, o destino que no responde


class AbstractTelemetryBackend(ABC):
    """
    Contrato de todo backend de telemetría: ciclo de vida, escritura en batch y
    estado legible desde afuera.

    El estado arranca en 'disabled' y lo mueve la implementación a medida que abre,
    escribe o falla. Los backends que conectan en segundo plano pasan por
    'connecting' y avisan el resultado desde su propio callback.
    """

    # Identidad del backend: nombra su sección de `telemetry` en config.yaml y es
    # con lo que se lo pide por nombre. Cada implementación la pisa.
    service_name = ""

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager
        self._status = STATUS_DISABLED

    @property
    def status(self) -> str:
        """Estado del backend, en el vocabulario STATUS_* de este módulo."""
        return self._status

    @abstractmethod
    def setup(self):
        """
        Abre lo que el backend necesite para escribir.

        Ni bloquea esperando al destino ni propaga errores: el resultado queda en
        `status`. Deshabilitado por config, no toca nada.
        """

    @abstractmethod
    def write(self, batch: list):
        """
        Escribe un lote de registros.

        Sin setup, deshabilitado o con el destino caído no hace nada. El batch que
        falla se pierde: acá no hay reintento ni buffer en disco.
        """

    @abstractmethod
    def close(self):
        """Libera la conexión y deja `status` en 'disabled'. Idempotente."""
