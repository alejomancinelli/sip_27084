"""
Archivo de solicitud: lo que el equipo manda para que le emitan una licencia.

Es un JSON legible y **sin nada secreto**: hashes de la huella, no los valores; lo que la
instalación declara en su config, no sus credenciales. Puede viajar por mail, y viaja por
mail, porque la planta no tiene internet y el portal no existe.

Además de la huella lleva **qué declara el equipo** —cuántas cámaras, qué features usan
sus pipelines—, para que el que firma no tenga que preguntarlo por teléfono y para que un
cupo mal emitido se note al comparar contra lo que el equipo pidió.

La contracara está en el repositorio de firma, que toma este archivo y devuelve el
`.lic`. El formato de los dos lo define este subsistema.
"""

import json
import os
import platform
import socket
from datetime import datetime, timezone

from system import paths, version
from system.config_manager import ConfigManager
from system.license import fingerprint, manager

#: Nombre con el que se guarda si no se pide otro.
REQUEST_FILENAME = "license_request.json"

#: Versión del formato de la solicitud, independiente de la de la licencia.
REQUEST_SCHEMA_VERSION = 1


def build_request(config: ConfigManager) -> dict:
    """Arma el contenido de la solicitud para este equipo."""
    components = fingerprint.read_components()
    pipelines = config.get("inference.pipelines", {}) or {}
    cameras = config.get("cameras", {}) or {}

    features = sorted({
        str(pipelines[slot].get("feature") or manager.DEFAULT_FEATURE)
        for slot in pipelines
        if isinstance(pipelines.get(slot), dict)
    })

    return {
        "schema": REQUEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": version.APP_VERSION,
        "client": str(config.get("project.client", "") or ""),
        "project_id": str(config.get("project.project_id", "") or ""),
        "device_id": str(config.get("system.device_id", "") or ""),
        "hostname": _read_hostname(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "fingerprint": {
            "components": components,
            "suggested_min_matches": min(fingerprint.DEFAULT_MIN_MATCHES, len(components)),
        },
        "declared": {
            "cameras": len(cameras),
            "features": features,
        },
    }


def save_request(config: ConfigManager, output_path: str | None = None) -> str:
    """
    Escribe la solicitud y devuelve la ruta donde quedó.

    Sin ruta la deja en la raíz del repo, que es donde el operador ya sabe buscar el
    `config.yaml`. Levanta `OSError` si no se pudo escribir: acá el llamador sí quiere
    enterarse, porque el usuario está esperando un archivo.
    """
    path = output_path or os.path.join(paths.PROJECT_ROOT, REQUEST_FILENAME)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(build_request(config), handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    return path


def _read_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""
