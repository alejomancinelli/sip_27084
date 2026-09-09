"""
Línea de comandos de la licencia, para el equipo sin pantalla y para la puesta en marcha.

    python -m system.license status              qué licencia hay y por qué vale o no
    python -m system.license fingerprint         las fuentes que se leen en este equipo
    python -m system.license request [-o ruta]   genera el archivo de solicitud
    python -m system.license install archivo     verifica un .lic y lo deja instalado

Hace lo mismo que la pestaña de diagnóstico de la interfaz, y existe porque el equipo de
gabinete corre headless y porque en la puesta en marcha todavía no hay nadie mirando una
pantalla. La lógica no está acá: acá está el `print`.
"""

import argparse
import sys

from system.config_manager import ConfigManager
from system.license import fingerprint, manager, request


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m system.license",
                                     description="Licencia del equipo.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Estado de la licencia instalada.")
    subparsers.add_parser("fingerprint", help="Fuentes de huella que se leen en este equipo.")

    request_parser = subparsers.add_parser("request", help="Genera el archivo de solicitud.")
    request_parser.add_argument("-o", "--output", default=None,
                                help="Ruta del archivo a escribir.")

    install_parser = subparsers.add_parser("install", help="Instala un archivo de licencia.")
    install_parser.add_argument("license_file", help="Ruta del .lic recibido.")

    args = parser.parse_args(argv)
    config = ConfigManager()

    if args.command == "status":
        return _print_status(config)
    if args.command == "fingerprint":
        return _print_fingerprint()
    if args.command == "request":
        return _write_request(config, args.output)
    return _install(config, args.license_file)


def _print_status(config: ConfigManager) -> int:
    status = manager.LicenseManager(config).get_status()

    print(f"Estado          : {manager.DESCRIPTIONS.get(status['state'], status['state'])}")
    print(f"Política        : {status['policy']}")
    if status["reason"]:
        print(f"Motivo          : {status['reason']}")
    print(f"Build compilado : {'sí' if status['is_compiled'] else 'no (desde fuentes)'}")

    if status["license_id"]:
        print(f"Licencia        : {status['license_id']}")
        print(f"Cliente         : {status['client']} / {status['project_id']}")
        print(f"Emitida         : {status['issued_at']}")
        print(f"Vence           : {status['expires_at'] or 'perpetua'}")
        if status["days_remaining"] is not None:
            print(f"Días restantes  : {status['days_remaining']}")
        print(f"Huella          : {status['fingerprint_matches']} de "
              f"{status['fingerprint_required']} exigidas")
        print(f"Cámaras         : {status['max_cameras'] if status['max_cameras'] else 'sin cupo'}")
        print(f"Features        : {', '.join(status['features']) or 'ninguno'}")

    return 0 if status["state"] in (manager.STATE_VALID, manager.STATE_UNLICENSED_BUILD) else 1


def _print_fingerprint() -> int:
    components = fingerprint.read_components()
    if not components:
        print("No se pudo leer ninguna fuente de huella en este equipo.")
        return 1

    for source in sorted(components):
        print(f"{source:<20} {components[source]}")
    print(f"\n{len(components)} fuentes; el default exige "
          f"{min(fingerprint.DEFAULT_MIN_MATCHES, len(components))}.")
    return 0


def _write_request(config: ConfigManager, output: str | None) -> int:
    try:
        path = request.save_request(config, output)
    except OSError as e:
        print(f"No se pudo escribir la solicitud: {e}", file=sys.stderr)
        return 1

    print(f"Solicitud escrita en {path}")
    print("Mandarla al proveedor para que emita la licencia.")
    return 0


def _install(config: ConfigManager, license_file: str) -> int:
    is_installed, message = manager.LicenseManager(config).install(license_file)
    print(message, file=sys.stdout if is_installed else sys.stderr)
    return 0 if is_installed else 1


if __name__ == "__main__":
    sys.exit(main())
