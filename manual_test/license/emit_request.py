"""
Prueba manual: genera el archivo de solicitud que se manda a firmar.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\license\\emit_request.py
    Linux:    .venv/bin/python manual_test/license/emit_request.py

Lee el config.yaml que tiene al lado (no el de la app) y escribe `license_request.json`
en esta misma carpeta. Es exactamente lo que hace `python -m system.license request` en
un equipo entregado; acá se separa en un script propio para poder correrlo con el config
de la prueba y sin ensuciar la raíz del repo.

Qué mirar:
    - **Las fuentes de huella que aparecen.** Es lo primero que hay que ver al estrenar
      esto en una máquina nueva. En Windows tienen que salir `board_uuid` y
      `board_serial`; en la Jetson, `module_serial` —el DMI ahí suele pedir root y
      simplemente no aparece, que es un dato y no una falla—. Con menos de dos fuentes
      estables, el N-de-M no protege gran cosa y conviene revisar por qué.
    - **Que no haya ningún valor en claro.** El archivo lleva hashes: si aparece un
      serial legible, algo está mal y no habría que mandarlo por mail.
    - **El bloque `declared`.** Dice cuántas cámaras y qué features tiene el config de al
      lado. Es lo que el que firma cruza contra la orden de compra: si acá dicen ocho
      cámaras y se vendieron cuatro, eso se ve antes de emitir y no en la planta.
    - Correrlo dos veces seguidas: los hashes tienen que ser idénticos. Si cambian entre
      corridas, hay una fuente inestable metida en la huella y una licencia emitida hoy
      dejaría de validar mañana.
    - Desenchufar un cable de red y volver a correrlo: la fuente `mac_...` de esa placa
      **no** tiene que desaparecer. Windows la sigue reportando con el cable afuera; si
      desapareciera, el N-de-M estaría contando algo que se mueve solo.
"""

import json
import sys
from pathlib import Path

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.config_manager import ConfigManager                          # noqa: E402
from system.license import fingerprint                                   # noqa: E402
from system.license.request import save_request                          # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_REQUEST_PATH = Path(__file__).resolve().parent / "license_request.json"


def main() -> int:
    config = ConfigManager(str(_CONFIG_PATH))

    components = fingerprint.read_components()
    print(f"\n── Huella de este equipo ──  ({len(components)} fuentes)")
    if not components:
        print("  NINGUNA. Sin huella no hay nada que atar: revisar por qué antes de emitir.")
    for source in sorted(components):
        print(f"  {source:<18} {components[source]}")

    stable = [source for source in components if source in fingerprint.STABLE_SOURCES]
    print(f"\n  Estables (sobreviven a un cambio de disco o de placa de red): "
          f"{', '.join(stable) or 'ninguna'}")
    if len(stable) < 2:
        print("  AVISO: con menos de dos fuentes estables, reemplazar una pieza puede "
              "dejar afuera al cliente.")

    path = save_request(config, str(_REQUEST_PATH))
    print(f"\n── Solicitud ──")
    print(f"  Escrita en {path}")
    print("  Mandarla al repositorio de firma para que emita el .lic.\n")

    print(json.dumps(json.loads(_REQUEST_PATH.read_text(encoding="utf-8")),
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
