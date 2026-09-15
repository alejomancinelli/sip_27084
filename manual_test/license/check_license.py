"""
Prueba manual: verifica una licencia y muestra qué arrancaría con ella.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\license\\check_license.py
    Linux:    .venv/bin/python manual_test/license/check_license.py

    --now 2028-01-01T00:00:00Z   evalúa como si el reloj marcara eso, sin tocar el del
                                 equipo. Es la forma rápida de probar vencimientos y
                                 retrocesos: mover el reloj de Windows de verdad también
                                 funciona, pero desincroniza el NTP y molesta al resto.
    --reset-clock                borra el estado de reloj guardado y arranca de cero.
    --license ruta               usa otro .lic en vez del de esta carpeta.
    --public-key BASE64          agrega una clave pública sólo para esta corrida.

**Este script fuerza `IS_COMPILED = True`.** Corriendo desde fuentes el subsistema no
enforcea nada y todo daría `unlicensed_build`, que es correcto pero no se puede probar.
Acá se simula el binario para poder ver el comportamiento real; es legítimo porque
`manual_test/` no entra en ningún build y porque simular el build **no saltea ninguna
verificación**: la firma se sigue exigiendo igual.

Lo mismo vale para `--public-key`: no es un bypass. Sin la clave privada del repo de
firma no se puede producir un token que valide, así que inyectar la pública sólo evita
tener que generar la tabla de claves del build para cada prueba. En un equipo entregado
esa tabla la escribe el repositorio de firma y la compila Nuitka; acá no hace falta.

La licencia y el estado de reloj se leen y se escriben **en esta carpeta**, no en la raíz
del repo: la prueba no ensucia la instalación de desarrollo.

Qué mirar:
    - **Sin `license.lic`**: estado `absent`. Las cuatro cámaras siguen arrancando —el
      feed en vivo no depende de la licencia, es lo que sirve para verificar el cableado
      en la puesta en marcha— pero ningún pipeline arranca y la publicación queda
      bloqueada sin importar la política. Antes esto frenaba el arranque del proceso
      entero; ahora en cambio abre en este modo restringido, porque un equipo entregado
      sin licencia necesita poder llegar a la pestaña de licencia sin una computadora
      aparte.
    - **Con una licencia válida de dos cámaras**: arrancan camera_1 y camera_2. Comentar
      camera_1 en el config y volver a correr: tienen que arrancar camera_2 y camera_3.
    - **Con `features: [core]`**: pipeline_2 no arranca y el motivo dice «feature no
      licenciado», distinto de «deshabilitado en config».
    - **Vencimiento**: `--now` un día después de `expires_at` da `expired`; un día antes,
      `valid`. Con la licencia a menos de 30 días, el reporte avisa aunque siga válida.
    - **Retroceso de reloj**: correr normal, después correr con `--now` un año atrás. El
      segundo tiene que dar `tampered` y NO `expired`: con la fecha movida, preguntar si
      venció no significa nada. `--reset-clock` vuelve a empezar.
    - **Editar el .lic a mano**: cambiar un dígito de `max_cameras` en el payload en
      base64 rompe la firma y da `invalid`. Es la prueba de que los entitlements no se
      pueden subir con un editor de texto.
    - **Política**: emitir con `policy: degrade` y una licencia vencida — la publicación
      de mediciones aparece BLOQUEADA. Con `warn`, permitida. Los entitlements se aplican
      en los dos casos.
"""

import argparse
import sys
from datetime import datetime, timezone
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
from system.license import fingerprint, manager, policy, public_key      # noqa: E402
from system.license.manager import LicenseManager                        # noqa: E402

_HERE = Path(__file__).resolve().parent
_CONFIG_PATH = _HERE / "config.yaml"
_LICENSE_PATH = _HERE / "license.lic"
_STATE_PATH = _HERE / "license_state.bin"

_TEST_KEY_ID = "manual-test"


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verifica una licencia con el config de al lado.")
    parser.add_argument("--now", default=None,
                        help="Instante ISO con el que evaluar, en vez del reloj del equipo.")
    parser.add_argument("--reset-clock", action="store_true",
                        help="Borra el estado de reloj guardado.")
    parser.add_argument("--license", default=str(_LICENSE_PATH), help="Ruta del .lic.")
    parser.add_argument("--public-key", default=None,
                        help="Clave pública en base64url, sólo para esta corrida.")
    args = parser.parse_args(argv)

    # El binario simulado: sin esto todo cae en `unlicensed_build` y no hay nada que probar.
    manager.IS_COMPILED = True

    if args.public_key:
        public_key.PUBLIC_KEYS[_TEST_KEY_ID] = args.public_key

    if args.reset_clock and _STATE_PATH.exists():
        _STATE_PATH.unlink()
        print(f"Estado de reloj borrado: {_STATE_PATH.name}")

    now = _parse_now(args.now)
    config = ConfigManager(str(_CONFIG_PATH))
    license_manager = LicenseManager(config, license_path=args.license,
                                     state_path=str(_STATE_PATH), now_provider=lambda: now)
    status = license_manager.get_status()

    _print_build(status)
    _print_license(status, Path(args.license))
    _print_cameras(config, license_manager)
    _print_pipelines(config, license_manager)
    _print_publishing(license_manager)
    _print_clock(now, license_manager)

    return 0 if status["state"] == manager.STATE_VALID else 1


# ── Bloques del reporte ──────────────────────────────────────────────────────

def _print_build(status: dict):
    print("\n── Build ─────────────────────────────────────────────────────────────")
    print("  Compilado           : sí (SIMULADO por esta prueba)")
    known = ", ".join(sorted(public_key.PUBLIC_KEYS)) or "ninguna"
    print(f"  Claves públicas     : {known}")
    if not public_key.has_any_key():
        print("  AVISO: sin clave pública ninguna licencia puede validar. Pasar --public-key,")
        print("         o generar la tabla del build (ver docs/licensing.md).")
    print(f"  Huella              : {len(status['fingerprint_sources'])} fuentes "
          f"({', '.join(status['fingerprint_sources']) or 'ninguna'})")


def _print_license(status: dict, license_path: Path):
    print("\n── Licencia ──────────────────────────────────────────────────────────")
    print(f"  Archivo             : {license_path.name} "
          f"({'presente' if license_path.is_file() else 'NO EXISTE'})")
    print(f"  Estado              : {manager.DESCRIPTIONS.get(status['state'], status['state'])}")
    print(f"  Política            : {status['policy']} "
          f"— {policy.DESCRIPTIONS.get(status['policy'], '')}")
    if status["reason"]:
        print(f"  Motivo              : {status['reason']}")

    if not status["license_id"]:
        return

    print(f"  Identificador       : {status['license_id']}")
    print(f"  Cliente / proyecto  : {status['client']} / {status['project_id']}")
    print(f"  Emitida             : {status['issued_at'][:10]}")
    print(f"  Vence               : {status['expires_at'][:10] if status['expires_at'] else 'perpetua'}")
    if status["days_remaining"] is not None:
        warning = "  <-- conviene renovar" if status["days_remaining"] <= manager.EXPIRY_WARNING_DAYS else ""
        print(f"  Días restantes      : {status['days_remaining']}{warning}")
    print(f"  Huella coincidente  : {status['fingerprint_matches']} de "
          f"{status['fingerprint_required']} exigidas")


def _print_cameras(config: ConfigManager, license_manager: LicenseManager):
    slots = tuple(config.get("cameras", {}) or {})
    allowed = license_manager.allowed_camera_slots(slots)

    print("\n── Cámaras ───────────────────────────────────────────────────────────")
    limit = license_manager.max_cameras
    print(f"  Declaradas en config: {len(slots)}")
    print(f"  Cupo de la licencia : {limit if limit is not None else 'sin cupo'}")
    for slot in slots:
        if slot in allowed:
            print(f"    {slot:<12} arranca")
        else:
            print(f"    {slot:<12} NO ARRANCA — sobre el cupo de la licencia")


def _print_pipelines(config: ConfigManager, license_manager: LicenseManager):
    pipelines = config.get("inference.pipelines", {}) or {}

    print("\n── Pipelines ─────────────────────────────────────────────────────────")
    if license_manager.state in manager.NO_LICENSE_STATES:
        print("  Sin licencia utilizable: ningún pipeline arranca, sin importar el feature.")
    else:
        granted = license_manager.features
        print(f"  Features licenciados: {', '.join(granted) or 'sin lista (no se restringe)'}")
    for slot in pipelines:
        # Se lee con el mismo helper que usará el cableado, así lo que se muestra acá es
        # exactamente lo que se compara contra la licencia — errores de config incluidos.
        feature = manager.read_feature(config.get(f"inference.pipelines.{slot}.feature", None))
        if not config.get(f"inference.pipelines.{slot}.enabled", True):
            verdict = "NO ARRANCA — deshabilitado en el config"
        elif not license_manager.has_feature(feature):
            verdict = "NO ARRANCA — feature no licenciado"
        else:
            verdict = "arranca"
        print(f"    {slot:<12} feature={feature:<28} {verdict}")


def _print_publishing(license_manager: LicenseManager):
    print("\n── Publicación de mediciones ─────────────────────────────────────────")
    if license_manager.is_publishing_allowed():
        print("  Permitida: las métricas salen al PLC y a la telemetría.")
    elif license_manager.state in manager.NO_LICENSE_STATES:
        print("  BLOQUEADA: sin licencia utilizable no se publica nada, sin importar la")
        print("  política por defecto. El canal Modbus SIGUE sirviendo: heartbeat, salud")
        print("  y palabras de estado se publican igual.")
    else:
        print("  BLOQUEADA por la política `degrade`. El canal Modbus SIGUE sirviendo:")
        print("  heartbeat, salud y palabras de estado se publican igual, y el bit de")
        print("  licencia dice por qué los números dejaron de moverse.")
    print(f"  Se reporta el estado inválido: "
          f"{'sí' if license_manager.should_report_invalid() else 'no'}")


def _print_clock(now: datetime, license_manager: LicenseManager):
    components = fingerprint.read_components()
    from system.license.clock import ClockGuard  # local: sólo lo usa este bloque

    guard = ClockGuard(str(_STATE_PATH), fingerprint.build_machine_key(components))
    last_seen = guard.read_last_seen_utc()

    print("\n── Reloj ─────────────────────────────────────────────────────────────")
    print(f"  Evaluado con        : {now.isoformat()}")
    print(f"  Último visto        : {last_seen.isoformat() if last_seen else 'sin estado previo'}")
    print(f"  Estado guardado en  : {_STATE_PATH.name}")
    if license_manager.state == manager.STATE_TAMPERED:
        print("  RETROCESO detectado: el reloj quedó más atrás que la última corrida.")
    print()


def _parse_now(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)

    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise SystemExit(f"--now no es una fecha ISO: {text!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    sys.exit(main())
