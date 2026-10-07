"""
Arma el entregable a partir de lo que dejó Nuitka.

    .venv\\Scripts\\python.exe build\\make_release.py [--dist build/out/main.dist]

Deja una carpeta lista para copiar al equipo de la planta:

    <APP_NAME>-<version>/
        program/                  el ejecutable y todo lo suyo — se reemplaza al actualizar
        installation/             lo de esta planta — NO se toca al actualizar
        _Entorno.cmd              la ruta de datos, y nada más
        <APP_NAME>.cmd            arranca la app, sin dejar consola
        <APP_NAME> sin pantalla.cmd
        Crear accesos directos.cmd   se corre una vez, ya instalado
        Verificar camara.cmd         que el equipo tenga lo que la cámara necesita

**En Linux —la Jetson— la forma es la misma** y cambian los lanzadores: `.sh` en vez de
`.cmd`, `_entorno.sh` en vez de `_Entorno.cmd`, el ejecutable es `main.bin`, y los accesos
directos son `.desktop`. Además se deja un `<APP_NAME>-<version>.tar.gz` al lado de la
carpeta: es lo que se copia al equipo, porque un pendrive en FAT32 o exFAT no guarda el
permiso de ejecución y la carpeta copiada así llega con un `main.bin` que no arranca. El
script corre en la plataforma del entregable, igual que `build.py`: no se arma un
entregable de Linux desde Windows.

El nombre sale de `system.app_name` en el `config.yaml` que se está empaquetando: es el
mismo texto que la ventana muestra en su título, así que el operador ve un solo nombre en
todos lados y no hay una segunda cadena que mantener sincronizada acá.

**`SIP_DATA_DIR` vive en un solo archivo**, `_Entorno.cmd`, y los lanzadores lo llaman con
`call`. Es la ruta de la que depende que la calibración de la planta sobreviva a una
actualización: repetida en cada lanzador, se corrige en uno y se olvida en los demás.

**Ninguno es un `.exe`.** Un ejecutable chico, sin firmar y sin historial es exactamente lo
que un antivirus corporativo suele poner en cuarentena al escribirlo —ver `README.md` si el
fork lo midió—, y un `.cmd` es una línea que la gente de sistemas del cliente puede leer y
aprobar. El precio es que un `.cmd` no puede llevar icono: es texto, y los iconos son
recursos de un binario. De ahí `Crear accesos directos.cmd`, que los crea **en el equipo y
en el lugar donde quedó instalado**, con el icono que el `.exe` ya lleva compilado.

**La app se larga con `start` y no deja consola.** El `.exe` es de subsistema Windows
—`--windows-console-mode=attach` en `build.py`— así que no abre consola propia; la que se
vería sin `start` es la del `cmd` que lo lanza, esperando a que el programa termine.

**Por qué separadas.** El programa se actualiza reemplazando `program/`. Si el
`config.yaml` con los valores calibrados y el mapa de registros que acordó el integrador
vivieran ahí adentro, cada actualización los borraría. `SIP_DATA_DIR` es lo que hace que el
ejecutable los busque afuera; sin la variable los busca al lado suyo, que funciona pero no
se puede actualizar.

**Lo que va suelto y por qué.** `stapipy` (Sentech) no se puede compilar: su `__init__.py`
es un cargador que se saca de `sys.modules` y vuelve a importar por `importlib` una
extensión nativa del mismo nombre, y Nuitka no puede seguir esa importación. `pypylon`
(Basler) sí se podría compilar, pero se elige no hacerlo: trae su propio runtime de pylon y
copiarlo suelto es una carpeta que se agrega o se saca sin recompilar, útil cuando todavía
no está decidido qué marca de cámara usa cada instalación. Los dos se copian enteros desde
el venv si están; si al fork no le hace falta soporte para una marca, sencillamente no está
instalado y esta etapa lo avisa y sigue.
"""

import argparse
import glob
import importlib.util
import os
import re
import shlex
import shutil
import struct
import sys
import unicodedata

_BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BUILD_DIR)
sys.path.insert(0, _REPO_ROOT)

from system.version import APP_VERSION  # noqa: E402

_DEFAULT_DIST = "build/out/main.dist"
_DEFAULT_OUT = "build/release"

_IS_WINDOWS = sys.platform == "win32"

# El ejecutable que deja Nuitka en `main.dist`. En Linux es `main.bin`; `main` queda como
# respaldo por si una versión de Nuitka lo deja sin sufijo.
_EXECUTABLE_NAMES = ("main.exe",) if _IS_WINDOWS else ("main.bin", "main")

# Paquetes que viajan como carpeta al lado del ejecutable en vez de compilados. Ver el
# docstring del módulo para el motivo de cada uno.
_LOOSE_PACKAGES = ("stapipy", "pypylon")

# Archivos con nombre fijo, propios de la maquinaria y no de la instalación: van siempre,
# si están. Los pesos del modelo NO están acá a propósito — un fork no tiene un nombre de
# archivo fijo para ellos, así que `models/` se copia entera más abajo.
_INSTALLATION_FILES = (
    ("config.yaml", "config.yaml"),
    ("system/modbus/register_map.yaml", "register_map.yaml"),
)

# Carpeta con los pesos del modelo: se copia entera porque su contenido —nombre de
# archivo, cuántos son— lo decide cada fork. Vacía si el fork todavía no tiene modelo
# propio, y entonces no se copia nada ni se avisa de nada.
_MODELS_DIR = "models"

# El único archivo donde vive la ruta de datos. Los lanzadores lo llaman con `call`, que
# corre en el mismo proceso, así que el `set` les queda puesto.
_ENV_NAME = "_Entorno.cmd"
_ENV_BODY = """@echo off
rem No se ejecuta directamente: lo llaman los demas .cmd de esta carpeta.
rem
rem SIP_DATA_DIR es lo que separa los datos de esta planta del programa: sin esta linea
rem el ejecutable busca su config al lado suyo, adentro de program\\, y una actualizacion
rem (que reemplaza program\\ entero) se llevaria puesta la calibracion.
rem
rem Vive en un archivo aparte y no en cada lanzador porque una ruta repetida en varios
rem archivos se corrige en uno y se olvida en los otros.
set "SIP_DATA_DIR=%~dp0installation"
"""

_LAUNCHER_BODY = """@echo off
rem {description}
rem
rem La ruta de datos la pone {env_name}, que es el unico archivo donde vive.
call "%~dp0{env_name}"
{launch}
"""

# Lo que sí queda en el log: un arranque que no llega a abrir la ventana no deja nada en
# pantalla, porque la consola se cierra con el `cmd`. Queda en installation\data\logs.
_DETACHED = 'start "" "%~dp0program\\main.exe"{arguments} %*'
_FOREGROUND = '"%~dp0program\\main.exe"{arguments} %*'


def _launchers(app_name: str, extension: str) -> tuple:
    """
    Nombre, descripción, qué le pasa al ejecutable y si el proceso queda suelto.

    Dos por defecto: con ventana y sin ventana. Un fork con una herramienta de línea de
    comandos que comparte el mismo ejecutable —un subcomando de calibración, por
    ejemplo— agrega su fila acá con `detach=False`, porque **`detach` es lo que decide si
    aparece una consola**: con `start` el `cmd` larga el proceso y se cierra sin dejar
    ventana, que es lo que quiere la app; una herramienta que tiene que mostrar algo en
    esa consola —el resultado de un ajuste, un chequeo— no se larga y se espera. En Linux
    no aplica: la consola la abre o no el `.desktop`, y el lanzador hace `exec` siempre.
    """
    return (
        (f"{app_name}{extension}", f"Arranca {app_name}.", "", True),
        (f"{app_name} sin pantalla{extension}",
         "Arranca sin ventana: equipo en gabinete sin monitor. No hace falta tocar el "
         "config.", "--headless", True),
    )


# Accesos directos en el escritorio, creados **en el equipo de la planta y en el lugar
# donde quedó instalado**. Eso no es un detalle: un `.lnk` que se arma al empaquetar y
# viaja adentro del entregable se sigue por object ID de NTFS y, copiado a otra máquina o
# a otra carpeta, se reapunta solo a la copia original sin avisar. Acá el que lo corre lo
# crea contra rutas absolutas de esta instalación, y nunca se copia.
#
# El icono sale de `program\main.exe`, que ya lo lleva compilado: no hay un `.ico` que
# mantener al lado ni que se pueda perder.
_SHORTCUTS_NAME = "Crear accesos directos.cmd"
_SHORTCUTS_BODY = """@echo off
rem Crea los accesos directos en el escritorio de este equipo.
rem
rem Se corre una vez, despues de copiar esta carpeta a donde va a quedar. Los accesos
rem apuntan a rutas absolutas de aca, asi que si despues se mueve la carpeta hay que
rem volver a correrlo.
rem
rem Los accesos NO viajan adentro del entregable a proposito: Windows sigue un .lnk por
rem el object ID del archivo, y uno copiado de otra maquina se reapunta solo a la copia
rem de origen sin decir nada. Un acceso que arranca otra instalacion publicaria numeros
rem de la calibracion equivocada sin fallar.
setlocal
set "BASE=%~dp0"
powershell -NoProfile -Command "{command}"
if errorlevel 1 (
  echo.
  echo No se pudieron crear los accesos. Se pueden hacer a mano: clic derecho en el
  echo lanzador principal, Enviar a, Escritorio.
)
echo.
pause
"""


def _shortcuts_command(app_name: str) -> str:
    """
    El PowerShell que crea los accesos, en una línea y sin comillas dobles.

    Sin comillas dobles porque va adentro de las del `cmd`, y `cmd` no tiene una forma
    prolija de anidarlas: todo lo de PowerShell va entre comillas simples, que además no
    interpretan la barra invertida de las rutas de Windows.
    """
    shortcuts = ((app_name, f"{app_name}.cmd", 7),)   # 7 = minimizado: tapa el parpadeo
    entries = ", ".join(
        f"@('{name}','{target}',{style})" for name, target, style in shortcuts)
    return "; ".join((
        "$ErrorActionPreference='Stop'",
        "try {",
        "$base=$env:BASE",
        "$desk=[Environment]::GetFolderPath('Desktop')",
        "$icon=(Join-Path $base 'program\\main.exe')+',0'",
        "$sh=New-Object -ComObject WScript.Shell",
        f"foreach($e in @({entries}))" + " {",
        "$l=$sh.CreateShortcut((Join-Path $desk ($e[0]+'.lnk')))",
        "$l.TargetPath=(Join-Path $base $e[1])",
        "$l.WorkingDirectory=$base",
        "$l.IconLocation=$icon",
        "$l.WindowStyle=$e[2]",
        # El nombre del producto no se repite acá: lo pone `build.py` en el `.exe`, y
        # duplicarlo en el cartelito de un acceso directo es tenerlo en dos lugares.
        f"$l.Description=($e[0]+' - version {APP_VERSION}')",
        "$l.Save()",
        "Write-Host ('  creado: '+$e[0])",
        "}",
        "Write-Host ('en '+$desk)",
        "} catch { Write-Host ('  ERROR: '+$_.Exception.Message); exit 1 }",
    ))


_CHECK_NAME = "Verificar camara.cmd"
_CHECK_BODY = """@echo off
rem Comprueba que este equipo tenga lo que hace falta para abrir las camaras.
rem
rem Se corre antes de arrancar por primera vez, y cuando una camara no conecta. No toca
rem nada: solo mira. Opcionalmente se le pasa una IP para ver si responde:
rem
rem     "Verificar camara.cmd" 10.8.1.130
rem
rem Lo que necesita cada fabricante NO es lo mismo, y esa es la diferencia que importa:
rem
rem   Sentech  el paquete viaja adentro de program\\, pero sus DLL nativas salen del
rem            SentechSDK, que tiene que estar INSTALADO en este equipo.
rem   Basler   pypylon trae su propio runtime de pylon, asi que no hay que instalar
rem            nada; lo unico que importa es que el paquete haya viajado.
rem
rem Las DLL que busca no estan escritas a mano: las saco del propio .pyd que viaja, al
rem armar el entregable. Sus nombres llevan la version del SDK adentro.
setlocal enabledelayedexpansion
set /a FALTAN=0
echo.
echo   Verificacion de soporte de camaras
echo   ----------------------------------
echo.
echo   [Sentech / Omron]
{sentech}
echo.
echo   [Basler]
{basler}
echo.
{network}
echo.
if !FALTAN! GTR 0 (
  echo   Faltan !FALTAN! DLL del SentechSDK en este equipo.
  echo   Hay que instalarlo, y tiene que ser el que corresponde a estas DLL: la version
  echo   va en el nombre del archivo, asi que otra version del SDK no sirve.
) else (
  echo   No falta nada de lo que se puede comprobar desde aca.
)
echo.
pause
"""

_CHECK_DLL = """where /q {dll}
if errorlevel 1 (
  echo     FALTA   {dll}
  set /a FALTAN+=1
) else (
  echo     ok      {dll}
)"""

_CHECK_NETWORK = """if "%~1"=="" (
  echo   [Red] sin IP. Se le puede pasar una: "Verificar camara.cmd" 10.8.1.130
) else (
  echo   [Red] probando %~1
  ping -n 2 %~1 >nul
  if errorlevel 1 (
    echo     sin respuesta   %~1
    echo     Ojo: no todas las camaras contestan ping, asi que esto solo no decide.
  ) else (
    echo     ok responde     %~1
  )
)"""


def _check_body(program: str) -> str:
    """El verificador, con las DLL que de verdad necesita el `.pyd` que se empaquetó."""
    sentech = []
    if os.path.isdir(os.path.join(program, "stapipy")):
        sentech.append("echo     ok      el paquete stapipy viaja en program\\")
        for dll in _external_dlls(program, "stapipy"):
            sentech.append(_CHECK_DLL.format(dll=dll))
    else:
        sentech.append("echo     n/a     stapipy no viaja: "
                       "esta compilacion no soporta Sentech")

    if os.path.isdir(os.path.join(program, "pypylon")):
        basler = ("echo     ok      pypylon viaja en program\\ - "
                  "no hace falta instalar el SDK de Basler")
    else:
        basler = "echo     n/a     pypylon no viaja: esta compilacion no soporta Basler"

    return _CHECK_BODY.format(sentech="\n".join(sentech), basler=basler,
                              network=_CHECK_NETWORK)


_STABLE_ABI_DLL = "python3.dll"


def _copy_stable_abi_dll(program: str) -> bool:
    """
    Copia `python3.dll` si algún paquete suelto lo necesita. True si hizo falta.

    **Medido.** Un `.pyd` compilado contra la ABI estable de Python no enlaza contra
    `python310.dll` sino contra `python3.dll`, que es un reenviador; una instalación de
    CPython lo trae, y un standalone de Nuitka **no lo copia**. `pypylon` es de esos, y sin
    este archivo su import falla con «DLL load failed while importing _pylon», que no
    nombra a la DLL que falta y manda a buscar el SDK de Basler, que no tiene nada que ver.

    Con el archivo al lado del `.exe` el paquete suelto carga y enumera sus transport
    layers. **No hace falta tocar `os.add_dll_directory()`**: se probó, y lo único que
    faltaba era esto —las DLL de pylon las encuentra solo, porque Python carga las
    extensiones con `LOAD_WITH_ALTERED_SEARCH_PATH`, que busca en el directorio del `.pyd`.

    Se copia **antes** de generar el verificador: así queda dentro de `program/` y no
    aparece como una dependencia externa que el operador tendría que ir a instalar.
    """
    needed = any(_STABLE_ABI_DLL in _imported_dlls(os.path.join(root, name))
                 for package in _LOOSE_PACKAGES
                 for root, _, names in os.walk(os.path.join(program, package))
                 for name in names if name.lower().endswith(".pyd"))
    if not needed or os.path.isfile(os.path.join(program, _STABLE_ABI_DLL)):
        return False
    source = os.path.join(sys.base_prefix, _STABLE_ABI_DLL)
    if not _copy_into(source, os.path.join(program, _STABLE_ABI_DLL)):
        raise SystemExit(
            f"Un paquete suelto necesita {_STABLE_ABI_DLL} y no está en {sys.base_prefix}. "
            f"Sin eso ese paquete no carga en el equipo de la planta.")
    return True


def _imported_dlls(pe_path: str) -> list:
    """
    Los nombres de DLL de la tabla de importaciones de un `.pyd` o `.exe`.

    Se lee el binario en vez de mantener una lista a mano porque la lista **cambia con la
    versión del SDK**: los nombres de las DLL de StApi llevan la versión adentro
    (`StApi_TL_MD_VC141_v1_2.dll`). Una lista escrita a mano queda vieja justo cuando
    alguien cambia el wheel, que es el momento en que hace falta que esté bien.
    """
    data = open(pe_path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    magic = struct.unpack_from("<H", data, pe + 0x18)[0]
    optional_size = struct.unpack_from("<H", data, pe + 0x14)[0]
    # El directorio de importaciones es la segunda entrada del data directory, que arranca
    # en 0x60 (PE32) o 0x70 (PE32+) del encabezado opcional.
    import_rva = struct.unpack_from(
        "<I", data, pe + 0x18 + (0x60 if magic == 0x10B else 0x70) + 8)[0]

    sections = []
    section_base = pe + 0x18 + optional_size
    for index in range(struct.unpack_from("<H", data, pe + 6)[0]):
        offset = section_base + index * 40
        virtual_size = struct.unpack_from("<I", data, offset + 8)[0]
        virtual_address = struct.unpack_from("<I", data, offset + 12)[0]
        raw_offset = struct.unpack_from("<I", data, offset + 20)[0]
        sections.append((virtual_address, virtual_size, raw_offset))

    def file_offset(rva: int):
        for virtual_address, virtual_size, raw_offset in sections:
            if virtual_address <= rva < virtual_address + max(virtual_size, 1):
                return raw_offset + (rva - virtual_address)
        return None

    names, index = [], 0
    while True:
        entry = file_offset(import_rva) + index * 20
        name_rva = struct.unpack_from("<I", data, entry + 12)[0]
        if name_rva == 0:
            return names
        start = file_offset(name_rva)
        names.append(data[start:data.index(b"\0", start)].decode())
        index += 1


# DLL que las trae Windows: no se buscan ni se avisan.
_SYSTEM_DLL_PREFIXES = ("api-ms-win-", "kernel32", "user32", "advapi32", "shell32",
                        "ole32", "oleaut32", "ws2_32", "msvcrt", "ntdll", "gdi32")


def _external_dlls(program: str, package: str) -> list:
    """
    De qué DLL depende un paquete suelto y **no viajan en el entregable**.

    Son las que tienen que estar en el equipo, puestas por el instalador del fabricante.
    El criterio no es una lista de nombres sino dónde está el archivo: lo que no está en
    `program/` y no lo trae Windows, viene de afuera.
    """
    package_dir = os.path.join(program, package)
    if not os.path.isdir(package_dir):
        return []
    inside = {name.lower() for _, _, names in os.walk(program) for name in names}
    needed: list = []
    for entry in sorted(os.listdir(package_dir)):
        if not entry.lower().endswith(".pyd"):
            continue
        for dll in _imported_dlls(os.path.join(package_dir, entry)):
            low = dll.lower()
            if low in inside or dll in needed:
                continue
            if any(low.startswith(prefix) for prefix in _SYSTEM_DLL_PREFIXES):
                continue
            needed.append(dll)
    return needed


# ── Linux ─────────────────────────────────────────────────────────────────────────
#
# Las plantillas de bash se completan con `.replace()` de marcas `@NOMBRE@` y no con
# `.format()`: bash usa llaves en cada `${...}` y en cada función, y duplicarlas todas
# vuelve ilegible justo lo que el integrador va a abrir para ver qué hace el archivo.

_LINUX_ENV_NAME = "_entorno.sh"
_LINUX_ENV_BODY = r"""# No se ejecuta directamente: lo cargan con `source` los demás .sh de esta carpeta.
#
# SIP_DATA_DIR es lo que separa los datos de esta planta del programa: sin esta línea el
# ejecutable busca su config al lado suyo, adentro de program/, y una actualización (que
# reemplaza program/ entero) se llevaría puesta la calibración.
#
# Vive en un archivo aparte y no en cada lanzador porque una ruta repetida en varios
# archivos se corrige en uno y se olvida en los otros.
export SIP_DATA_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/installation"
"""

_LINUX_LAUNCHER_BODY = r"""#!/bin/bash
# @DESCRIPTION@
#
# La ruta de datos la pone @ENV_NAME@, que es el único archivo donde vive. Un arranque que
# no llega a abrir la ventana deja el motivo en installation/data/logs.
BASE="$(dirname "$(readlink -f "$0")")"
source "$BASE/@ENV_NAME@"
exec "$BASE/program/@EXECUTABLE@"@ARGUMENTS@ "$@"
"""

# El acceso se crea **en el equipo y contra el lugar donde quedó instalado**, igual que en
# Windows: un `.desktop` que viaja en el entregable apunta a la instalación del equipo
# donde se armó, y arrancar otra instalación publica con otra calibración sin fallar.
_LINUX_SHORTCUTS_BODY = r"""#!/bin/bash
# Crea el acceso a la app en el menú de aplicaciones y en el escritorio de este equipo.
#
# Se corre una vez, después de copiar esta carpeta a donde va a quedar, con el usuario que
# va a usar la app:
#
#     bash "Crear accesos directos.sh"
#
# El acceso apunta a rutas absolutas de acá, así que si después se mueve la carpeta hay
# que volver a correrlo. NO viaja adentro del entregable a propósito: uno copiado de otro
# equipo arrancaría la instalación de ese equipo, con su calibración, sin fallar.
set -e
BASE="$(dirname "$(readlink -f "$0")")"
NAME=@NAME@
LAUNCHER="$BASE/$NAME.sh"
ICON="$BASE/program/"@ICON@
ENTRY_ID=@ENTRY_ID@

# Exec= de un .desktop tiene su propio escapado, que además cambia entre escritorios.
# Una ruta con estos caracteres no se escapa: se corta antes de escribir un acceso roto.
case "$LAUNCHER" in
    *[\"\`\$\\]*)
        echo "  La ruta tiene comillas, \$, \` o \\ y un acceso directo no la puede llamar:"
        echo "    $LAUNCHER"
        echo "  Mover la carpeta a una ruta sin esos caracteres y volver a correr esto."
        exit 1
        ;;
esac

write_entry() {
    printf '%s\n' \
        "[Desktop Entry]" \
        "Type=Application" \
        "Name=$NAME" \
        "Comment=$NAME - versión @VERSION@" \
        "Exec=\"$LAUNCHER\"" \
        "Path=$BASE" \
        "Icon=$ICON" \
        "Terminal=false" \
        "Categories=Utility;" > "$1"
    chmod +x "$1"
}

APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
write_entry "$APPS/$ENTRY_ID.desktop"
echo "  creado: $NAME, en el menú de aplicaciones"

# xdg-user-dir devuelve el home cuando el escritorio no está configurado, y en un equipo
# en castellano la carpeta es ~/Escritorio: por eso no se escribe ~/Desktop a mano.
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
if [ -n "$DESKTOP" ] && [ "$DESKTOP" != "$HOME" ] && [ -d "$DESKTOP" ]; then
    write_entry "$DESKTOP/$ENTRY_ID.desktop"
    # GNOME no lanza un .desktop del escritorio que no esté marcado como confiable.
    gio set "$DESKTOP/$ENTRY_ID.desktop" metadata::trusted true 2>/dev/null || true
    echo "  creado: $NAME, en $DESKTOP"
    echo "  Si el icono aparece sin permiso para ejecutar: clic derecho, Permitir ejecutar."
else
    echo "  Este usuario no tiene carpeta de escritorio: el acceso queda sólo en el menú."
fi
"""

# Estático, a diferencia del de Windows: allá las DLL que faltan se sacan del `.pyd` al
# armar el entregable, porque el equipo no trae con qué leerlas. Acá `ldd` ya está en el
# equipo y contesta contra el enlazador de ESE equipo, que es justo la pregunta.
_LINUX_CHECK_BODY = r"""#!/bin/bash
# Comprueba que este equipo tenga lo que hace falta para abrir las cámaras.
#
# Se corre antes de arrancar por primera vez, y cuando una cámara no conecta. No toca
# nada: sólo mira. Opcionalmente se le pasa una IP para ver si responde:
#
#     bash "Verificar camara.sh" 10.8.1.130
#
# Lo que necesita cada fabricante NO es lo mismo, y esa es la diferencia que importa:
#
#   Sentech  el paquete viaja adentro de program/, pero sus bibliotecas nativas salen del
#            SentechSDK, que tiene que estar INSTALADO en este equipo, junto con la
#            variable GENICAM_GENTL64_PATH que deja su instalador.
#   Basler   pypylon trae su propio runtime de pylon, así que no hay que instalar nada;
#            lo único que importa es que el paquete haya viajado.
#
# Las bibliotecas que busca no están escritas acá: se las pregunta a `ldd`, sobre las
# extensiones que viajan. Sus nombres llevan la versión del SDK adentro.
BASE="$(dirname "$(readlink -f "$0")")"
PROGRAM="$BASE/program"
MISSING=0

# Lo que Nuitka dejó en program/ lo carga el ejecutable antes que nadie: sin esto, `ldd`
# daría por faltante una biblioteca que sí viaja.
export LD_LIBRARY_PATH="$PROGRAM${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

check_libs() {
    local libs
    libs="$(find "$PROGRAM/$1" -name '*.so*' -type f -exec ldd {} \; 2>/dev/null \
            | awk '/not found/ {print $1}' | sort -u)"
    if [ -z "$libs" ]; then
        echo "    ok      ldd encuentra todas sus bibliotecas"
        return
    fi
    while IFS= read -r lib; do
        echo "    FALTA   $lib"
        MISSING=$((MISSING + 1))
    done <<< "$libs"
}

echo
echo "  Verificación de soporte de cámaras"
echo "  ----------------------------------"
echo
echo "  [Sentech / Omron]"
if [ -d "$PROGRAM/stapipy" ]; then
    echo "    ok      el paquete stapipy viaja en program/"
    check_libs stapipy
    if [ -n "$GENICAM_GENTL64_PATH" ]; then
        echo "    ok      GENICAM_GENTL64_PATH=$GENICAM_GENTL64_PATH"
    else
        echo "    FALTA   GENICAM_GENTL64_PATH: la deja el instalador del SentechSDK en"
        echo "            /etc/environment, y entra recién al volver a iniciar sesión"
        MISSING=$((MISSING + 1))
    fi
else
    echo "    n/a     stapipy no viaja: esta compilación no soporta Sentech"
fi
echo
echo "  [Basler]"
if [ -d "$PROGRAM/pypylon" ]; then
    echo "    ok      pypylon viaja en program/ - no hace falta instalar el SDK de Basler"
    check_libs pypylon
else
    echo "    n/a     pypylon no viaja: esta compilación no soporta Basler"
fi
echo
if [ -z "$1" ]; then
    echo "  [Red] sin IP. Se le puede pasar una: bash \"Verificar camara.sh\" 10.8.1.130"
else
    echo "  [Red] probando $1"
    if ping -c 2 -W 2 "$1" > /dev/null 2>&1; then
        echo "    ok responde     $1"
    else
        echo "    sin respuesta   $1"
        echo "    Ojo: no todas las cámaras contestan ping, así que esto solo no decide."
    fi
fi
echo
if [ "$MISSING" -gt 0 ]; then
    echo "  Faltan $MISSING cosas en este equipo. Las bibliotecas de Sentech se resuelven"
    echo "  instalando el SentechSDK, y tiene que ser el que corresponde a esta compilación:"
    echo "  la versión va en el nombre del archivo, así que otra versión del SDK no sirve."
else
    echo "  No falta nada de lo que se puede comprobar desde acá."
fi
"""


def _fill(template: str, **values: str) -> str:
    """Reemplaza cada `@CLAVE@` de una plantilla de bash por su valor."""
    for key, value in values.items():
        template = template.replace(f"@{key.upper()}@", value)
    return template


def _desktop_entry_id(app_name: str) -> str:
    """
    El nombre del `.desktop`: el de la app, sin tildes ni espacios.

    El escritorio lo usa como identificador de la aplicación, y uno con espacios o tildes
    anda en un escritorio y en otro no. El nombre que ve el operador va adentro, en `Name=`.
    """
    plain = unicodedata.normalize("NFKD", app_name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "app"


def _program_icon() -> str:
    """
    El icono del programa, relativo a la raíz del repo y a `program/`.

    Es el `_ICON` de `build.py`, el mismo que compila en el `.exe`: se lee de ahí para que
    un fork que cambia el icono no tenga que acordarse de cambiarlo en dos archivos.
    """
    spec = importlib.util.spec_from_file_location(
        "build_script", os.path.join(_BUILD_DIR, "build.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._ICON


def _write_linux_scripts(release: str, program: str, executable: str, app_name: str):
    """Lanzadores, accesos directos y verificador de cámara, en bash."""
    _write_script(os.path.join(release, _LINUX_ENV_NAME), _LINUX_ENV_BODY)
    print(f"    {_LINUX_ENV_NAME}     la ruta de datos, y nada más")
    for launcher_name, description, arguments, _ in _launchers(app_name, ".sh"):
        _write_script(os.path.join(release, launcher_name), _fill(
            _LINUX_LAUNCHER_BODY, description=description, env_name=_LINUX_ENV_NAME,
            executable=executable, arguments=f" {arguments}" if arguments else ""))
        print(f"    {launcher_name}")

    # El icono lo pide el `.desktop` por ruta; si el fork lo puso fuera de los datos que
    # compila `build.py`, se lo copia para que el acceso no quede sin icono.
    icon = _program_icon()
    _copy_into(os.path.join(_REPO_ROOT, icon), os.path.join(program, icon))
    shortcuts_name = "Crear accesos directos.sh"
    _write_script(os.path.join(release, shortcuts_name), _fill(
        _LINUX_SHORTCUTS_BODY, name=shlex.quote(app_name), icon=shlex.quote(icon),
        entry_id=shlex.quote(_desktop_entry_id(app_name)), version=APP_VERSION))
    print(f"    {shortcuts_name}   se corre una vez, ya instalado")

    check_name = "Verificar camara.sh"
    _write_script(os.path.join(release, check_name), _LINUX_CHECK_BODY)
    print(f"    {check_name}         pregunta a ldd qué falta, en el equipo")


def _write_windows_scripts(release: str, program: str, app_name: str):
    """Lanzadores, accesos directos y verificador de cámara, en `cmd`."""
    _write_text(os.path.join(release, _ENV_NAME), _ENV_BODY)
    print(f"    {_ENV_NAME}    la ruta de datos, y nada más")
    for launcher_name, description, arguments, detach in _launchers(app_name, ".cmd"):
        launch = (_DETACHED if detach else _FOREGROUND).format(
            arguments=f" {arguments}" if arguments else "")
        _write_text(os.path.join(release, launcher_name),
                    _LAUNCHER_BODY.format(description=description, env_name=_ENV_NAME,
                                          launch=launch))
        print(f"    {launcher_name}{'' if detach else '   (deja la consola a la vista)'}")

    _write_text(os.path.join(release, _SHORTCUTS_NAME),
                _SHORTCUTS_BODY.format(command=_shortcuts_command(app_name)))
    print(f"    {_SHORTCUTS_NAME}   se corre una vez, ya instalado")

    _write_text(os.path.join(release, _CHECK_NAME), _check_body(program))
    external = _external_dlls(program, "stapipy")
    print(f"    {_CHECK_NAME}         busca {len(external)} DLL del SDK en el equipo")


def _write_script(path: str, text: str):
    """Un script de bash: saltos de Unix y permiso de ejecución."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.chmod(path, 0o755)


def _archive(release: str) -> str:
    """
    Empaqueta el entregable en un `.tar.gz` al lado de la carpeta. Devuelve su ruta.

    El tar guarda el permiso de ejecución; la carpeta copiada a un pendrive en FAT32 o
    exFAT lo pierde, y el equipo recibe un `main.bin` que no arranca y lanzadores que no
    se pueden ejecutar.
    """
    parent, name = os.path.split(release)
    return shutil.make_archive(release, "gztar", root_dir=parent, base_dir=name)


def _site_packages() -> str:
    """El `site-packages` del venv del repo, que en Windows y en Linux no está en el mismo lugar."""
    venv = os.path.join(_REPO_ROOT, ".venv")
    if _IS_WINDOWS:
        return os.path.join(venv, "Lib", "site-packages")
    matches = sorted(glob.glob(os.path.join(venv, "lib", "python3*", "site-packages")))
    return matches[0] if matches else os.path.join(venv, "lib", "site-packages")


def _find_executable(dist: str) -> str:
    """El nombre del ejecutable que dejó Nuitka en `dist`, o vacío si no hay ninguno."""
    for name in _EXECUTABLE_NAMES:
        if os.path.isfile(os.path.join(dist, name)):
            return name
    return ""


def _write_text(path: str, text: str):
    """Un archivo de texto con saltos de Windows: los lee el `cmd` y el Notepad."""
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(text)


def _copy_into(source: str, target: str) -> bool:
    """Copia un archivo o una carpeta, creando lo que falte. False si no estaba."""
    if not os.path.exists(source):
        return False
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    if os.path.isdir(source):
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".gitkeep"))
    else:
        shutil.copy2(source, target)
    return True


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def _app_name(config_path: str) -> str:
    """
    `system.app_name` del config que se está por empaquetar, saneado para nombre de archivo.

    Es el mismo texto que ve el operador en el título de la ventana: un solo nombre en
    todos lados, en vez de mantenerlo sincronizado a mano entre el config y este script.
    """
    import yaml

    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    name = str((config.get("system", {}) or {}).get("app_name", "") or "").strip()
    if not name:
        raise SystemExit(f"{config_path} no declara `system.app_name`.")
    return _INVALID_FILENAME_CHARS.sub("_", name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Arma el entregable.")
    parser.add_argument("--dist", default=_DEFAULT_DIST, help="el main.dist de Nuitka")
    parser.add_argument("--out", default=_DEFAULT_OUT, help="dónde dejar el entregable")
    parser.add_argument("--name", default="", help="nombre de la carpeta; vacío = automático")
    parser.add_argument("--no-archive", action="store_true", dest="no_archive",
                        help="en Linux, no armar el .tar.gz")
    args = parser.parse_args()

    if not _IS_WINDOWS and not sys.platform.startswith("linux"):
        raise SystemExit(f"Sin entregable para {sys.platform}: sólo Windows y Linux.")
    dist = os.path.join(_REPO_ROOT, args.dist) if not os.path.isabs(args.dist) else args.dist
    executable = _find_executable(dist)
    if not executable:
        raise SystemExit(f"No hay {' ni '.join(_EXECUTABLE_NAMES)} en {dist}. "
                         f"Compilar primero — ver build/README.md.")

    app_name = _app_name(os.path.join(_REPO_ROOT, "config.yaml"))
    name = args.name or f"{app_name}-{APP_VERSION}"
    out_root = os.path.join(_REPO_ROOT, args.out) if not os.path.isabs(args.out) else args.out
    release = os.path.join(out_root, name)
    program, installation = os.path.join(release, "program"), os.path.join(release, "installation")
    if os.path.isdir(release):
        shutil.rmtree(release)

    print(f"  {name}")
    shutil.copytree(dist, program, ignore=shutil.ignore_patterns("__pycache__"))
    print(f"    program/        {_size_mb(program):6.1f} MB, {_count(program)} archivos")

    site_packages = _site_packages()
    for package in _LOOSE_PACKAGES:
        source = os.path.join(site_packages, package)
        if not _copy_into(source, os.path.join(program, package)):
            # Se avisa y se sigue: cada paquete es el soporte de **una** marca de cámara,
            # y no tener el de una marca que esta planta no usa no es motivo para no
            # armar el entregable. `Verificar camara` lo dice en el equipo.
            print(f"    program/{package}/  FALTA en el venv: el entregable queda sin "
                  f"soporte para esa marca")
            continue
        print(f"    program/{package}/  suelto, {_size_mb(os.path.join(program, package)):.0f} MB")

    # `python3.dll` es cosa de Windows: en Linux una extensión de ABI estable resuelve sus
    # símbolos contra el proceso que la carga, y no hay un reenviador que copiar.
    if _IS_WINDOWS and _copy_stable_abi_dll(program):
        print(f"    program/{_STABLE_ABI_DLL}   lo pide un paquete suelto; Nuitka no lo copia")

    missing = []
    for source_rel, target_rel in _INSTALLATION_FILES:
        if not _copy_into(os.path.join(_REPO_ROOT, source_rel),
                          os.path.join(installation, target_rel)):
            missing.append(source_rel)
    # Sin modelo todavía no hay nada que copiar, y no es un faltante: no entra a
    # `missing`. El tamaño de lo que sí haya ya entra en el total que se imprime abajo.
    _copy_into(os.path.join(_REPO_ROOT, _MODELS_DIR),
               os.path.join(installation, _MODELS_DIR))
    print(f"    installation/   {_size_mb(installation):6.1f} MB")

    if _IS_WINDOWS:
        _write_windows_scripts(release, program, app_name)
    else:
        _write_linux_scripts(release, program, executable, app_name)

    if missing:
        print("\n  FALTAN, hay que ponerlos a mano en installation/:")
        for item in missing:
            print(f"    {item}")

    print(f"\n  Total: {_size_mb(release):.1f} MB en {release}")
    if not _IS_WINDOWS and not args.no_archive:
        archive = _archive(release)
        print(f"  Para copiar al equipo: {archive} "
              f"({os.path.getsize(archive) / 1e6:.0f} MB)")
    return 0


def _size_mb(path: str) -> float:
    return sum(os.path.getsize(os.path.join(root, name))
               for root, _, names in os.walk(path) for name in names) / 1e6


def _count(path: str) -> int:
    return sum(len(names) for _, _, names in os.walk(path))


if __name__ == "__main__":
    sys.exit(main())
