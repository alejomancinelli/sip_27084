# Plan: compilar el entregable en la Jetson — estado, hallazgos y lo que falta

Para quien retome esto en la Jetson, persona o sesión nueva. Leer `CLAUDE.md` primero; este
plan no repite el mapa ni las decisiones, sólo dice dónde estamos, qué se midió, qué se
encontró y qué falta. La versión anterior —el plan antes de probar nada— está en `5b40f05`.

## Dónde estamos (2026-10-09, después de la primera ronda)

**El equipo de build es también el de prueba.** Seeed reComputer Industrial J201 con Jetson
Orin NX 8 GB, L4T R36.4.0 (JetPack 6.1), TensorRT 10.3, CUDA 12.6, Python 3.10.12 de Debian.
Usuario `iea`, repo en `/home/iea/Documents/sip_27084`, rama `migration/belt-monitor-27084`.
Nuitka **4.2.2** en el venv (`pip install -c setup/jetson/constraints.txt nuitka ordered-set
zstandard`), modo de potencia **20W**.

**El modo acelerado sirve, y ya está en los scripts.** Compilado, el equipo arranca, valida
una licencia firmada, abre la UI, sirve el Modbus y mide con el `.engine` en CUDA al mismo
ritmo que desde fuentes. Primero se probó a mano —con el comando de abajo y una carpeta
armada a mano, hoy `~/release_test_manual`— y después se llevó a la maquinaria (paso 5) y
se repitió de punta a punta con `build.py` → `make_release.py` → el `.tar.gz` descomprimido
en `~/release_test`. **Los cambios del paso 5 están commiteados**, uno por tema, para el
PR al template: ver «Los commits para el PR».

**El cierre colgado está resuelto.** Con SIGTERM el binario se trababa y a veces abortaba con
core dump; eran dos causas, una sólo del compilado y otra también desde fuentes. Ver «El
cierre colgado». Los dos arreglos están entre esos commits.

**Lo que nunca se probó compilado:** la cámara real (no estaba conectada) y el RTSP (está
apagado en la planta).

**Lo de `build/release/` y las dos carpetas de `~/` son de laboratorio:** el binario lleva
compilada la clave `iea-test`, y las carpetas de prueba, la licencia de esta Jetson.

## ¿Y el standalone?

**No hace falta, y no se terminó nunca.** El intento anterior no dejó números: `build/out`
quedó vacío, sin `.c` ni `.o`, así que no se sabe hasta dónde llegó. Lo que se sabe es por qué
no terminaba: con `--standalone` Nuitka traduce a C todo lo que `main.py` alcanza —torch,
torchvision, ultralytics y lo que ellos importan, miles de módulos— y con 8 GB eso son horas y
un OOM. El acelerado tarda **1,7 minutos** y no toca el swap.

Lo que el standalone daría y el acelerado no:

- **No depender del Python del sistema.** El acelerado usa la librería estándar de
  `/usr/lib/python3.10`, que en JetPack está siempre. Si un `apt upgrade` de la planta sube
  el parche de `python3.10`, el binario sigue con su 3.10.12 embebido contra una stdlib más
  nueva del mismo 3.10: compatible por diseño, pero no es lo mismo que se probó.
- **Menos `.py` sueltos.** En el acelerado todo lo de terceros es editable; en el standalone
  también queda algo suelto (`pypylon`), así que la diferencia es de grado y no de clase.
  Ver «La seguridad del modo acelerado».

Lo que no daría: autosuficiencia —CUDA, TensorRT, el driver y GStreamer tienen que venir del
JetPack de la planta igual— ni velocidad, que está en CUDA y no en el Python que lo llama. Y
traería problemas propios: `cryptography` compilada no anda con Nuitka 4.2.2 en ningún modo
(ver hallazgo 3), y el cierre colgado no se sabe si también le pasa.

**El standalone sigue siendo la forma de Windows.** El cambio del paso 5 es que la Jetson use
el acelerado, no sacar el standalone de `build.py`.

## El comando que anduvo

Desde la raíz del repo, con el venv en su lugar:

    .venv/bin/python -m nuitka \
        --enable-plugin=pyside6 \
        --python-flag=no_docstrings \
        --python-flag=no_site \
        --file-reference-choice=runtime \
        --no-deployment-flag=excluded-module-usage \
        --assume-yes-for-downloads \
        --output-dir=build/out-lab-loose \
        --report=build/out-lab-loose/compilation-report.xml \
        --follow-import-to=system --follow-import-to=tools --follow-import-to=ui \
        --include-package=system --include-package=tools --include-package=ui \
        main.py

Respecto del `--dry-run` de hoy: sin `--standalone`, sin los `--include-data-dir` y sin
`--include-package=gpiod` (en acelerado `gpiod` va suelto como el resto). Se agregan
`no_site`, `--file-reference-choice=runtime` (hallazgo 1) y los `--follow-import-to` /
`--include-package` de lo nuestro. **`cryptography` no se compila** (hallazgo 3).

La carpeta de prueba, con la forma que va a tener el entregable:

    ~/release_test/
        program/
            main.bin
            ui/styles/  ui/icons/          copiados del repo
            site-packages/                 cp -a de .venv/lib/python3.10/site-packages,
                                           sin nuitka, ordered_set, zstandard, pytest,
                                           _pytest, pluggy, iniconfig ni py.py
        installation/                      config.yaml, register_map.yaml, models/, license.lic
        lanzar.sh                          exporta SIP_DATA_DIR, PYTHONPATH=program/site-packages
                                           y PYTHONNOUSERSITE=1, y hace exec de main.bin

Para la prueba de inferencia, `installation/config.yaml` tiene la cámara en `driver: mock`.
**Esa carpeta es de laboratorio y no se entrega:** tiene la licencia de prueba de esta Jetson.

## Resultados, paso por paso

| Paso | Resultado |
|---|---|
| 1. Build | OK. 1,7 min, el gcc más grande 251 MB, `MemAvailable` nunca bajó de 2,3 GB, swap intacto |
| 2. Qué salió | OK. `libpython` **estática** (el `libpython3.10-pic.a` de Debian): `ldd` sólo nombra libz, libm, libexpat y libc. 115 módulos compilados (system 64, ui 37, tools 13, `__main__`), ningún tercero |
| 3. Carpeta | OK. `site-packages` 2855 → 2802 MB sacando lo de build y test; carpeta 2,8 GB. El único `.pth` es `distutils-precedence.pth`, y no hace falta |
| 4.1 Arranque | OK. En `/proc/<pid>/maps` no hay nada de `.venv` ni de `~/.local`; de `dist-packages` sólo `gi`, que entra por su symlink, como se diseñó. El venv estuvo movido a `.venv.off` durante las pruebas |
| 4.2 Rutas | FAIL esperado. `sys.executable` embebido = `.venv/bin/python` del build (hallazgo 2) |
| 4.3 Sin licencia | OK. Estado `absent`, ningún pipeline arranca, barra roja, cartel al abrir, bit 4 del reg 1 prendido |
| 4.4 Inyección | OK. Un `_public_key.py`, `verify.py` y `manager.py` plantados en `program/system/license/` y en `program/site-packages/system/license/` no se importan nunca: gana lo compilado |
| 4.5 UI | OK. Tema oscuro (los QSS cargan de `program/ui/styles`), iconos, navegación y diagnóstico. Cierra limpio con la X en 2 s |
| 4.6 Modbus | OK. FC03 al 502, latido avanzando, FC06 rechazado con 0x02 |
| 4.7 Inferencia | OK. Con una licencia firmada con `iea-test` para esta huella: «Licencia válida», el `.engine` abre en CUDA (primera inferencia 3,2 s) y el ciclo mide igual que desde fuentes (tabla de abajo) |
| 4.8 Cámara real | No probado: no estaba conectada |
| 4.9 GPIO | OK. `/dev/gpiochip0` abierto con hardware real, palabras 82/83 publicadas |
| 4.10 RTSP | No probado: apagado en la planta |

**Tiempo del ciclo de inferencia**, registro 12 (`inference_time_ms`) leído por Modbus cada
200 ms durante 60 s, cámara mock de 1920×1200, a 20W, misma `installation/` en los dos casos:

| | mediana | p10 | p90 | mín | máx |
|---|---|---|---|---|---|
| Compilado | 51 ms | 39 | 64 | 33 | 92 |
| Desde fuentes | 50 ms | 41 | 66 | 34 | 95 |

El registro 12 es el ciclo entero —ajuste ×4,6, modelo, refinamiento de fondo oscuro,
analyzer—, así que no se compara con los 24,8 ms de la medición anterior, que eran del modelo
solo y con frames de 2048×1536. La comparación que vale es la de la tabla: compilar no cambia
el ritmo. Un dato suelto sin explicar: una vez, desde fuentes, la primera inferencia tardó
33 s en vez de ~4 s; no se repitió ni se investigó.

## Hallazgos

### 1. Sin `--file-reference-choice=runtime`, en la planta faltan los QSS y los iconos — resuelto

En acelerado Nuitka usa por defecto `original`: el `__file__` de cada módulo compilado es la
ruta del fuente **en la máquina de build**. `ui/theme.py:127` y `ui/main_window.py:569` buscan
los QSS y los iconos al lado de su `__file__`, así que en esta Jetson la prueba pasaba —el
repo está en esa ruta— y en la planta la UI abriría sin estilos ni icono. `system/modbus/schema.py:302`
hace lo mismo con el mapa de respaldo, inofensivo porque primero mira `DATA_DIR`. Con `runtime`
el `__file__` es relativo al binario (`program/ui/theme.py`) y los datos se encuentran donde
`make_release.py` ya los pone. Nuitka avisa «non-default file reference mode … may cause run
time issues»: no apareció ninguno. Además saca del binario las rutas del repo de build.

### 2. `paths._app_dir()` resuelve al venv de build — para el paso 5

Compilado, `_app_dir()` usa `sys.executable`, que en acelerado es el intérprete de build
embebido (`/home/iea/Documents/sip_27084/.venv/bin/python`). Hoy no rompe nada porque los
lanzadores siempre exportan `SIP_DATA_DIR` y nadie llama a `app_file()`; sin la variable, el
equipo buscaría su config en un venv que en la planta no existe.

El arreglo **no** es reemplazar `sys.executable` por `__compiled__.containing_dir`: ese campo
cambia de sentido según el modo. En acelerado es la carpeta del binario (`program/`); en
standalone no-onefile Nuitka le aplica un `dirname` más y es la carpeta **padre** de
`main.dist` (ver `CodeTemplatesConstants.py` de Nuitka). Va según `__compiled__.standalone`:
el camino de hoy para el standalone, que está verificado en Windows, y `containing_dir` para
el acelerado.

### 3. `cryptography` compilada no verifica licencias — decidido: suelta + canario

Con `--follow-import-to=cryptography` el binario decía «Falta la librería 'cryptography':
este build no puede verificar licencias», así que **ninguna licencia validaba nunca** y el
equipo quedaba para siempre en puesta en marcha. El `try/except ImportError` de `verify.py`
lo dejaba pasar en silencio.

La causa: `cryptography` 50.0.2 trae una extensión de Rust (PyO3) con inicialización
multifase —`PYTHONVERBOSE` muestra `entrypoint returned module def`—, y el cargador de
extensiones de Nuitka 4.2.2 la deja **sin atributos**: `dir(_rust)` da `[]` y
`from cryptography.hazmat.bindings._rust import exceptions` falla. Copiar `_rust.abi3.so` a
`program/cryptography/hazmat/bindings/`, donde lo busca el paquete compilado, hace que cargue,
pero vacía igual. Suelta, cargada por el `ExtensionFileLoader` de CPython, anda. No aparece
como problema conocido de Nuitka.

Decisión del usuario: **`cryptography` suelta, y un canario en `verify.py`** (paso 5).

### 4. Dos symlinks rotos en el TensorRT del sistema — resuelto en esta Jetson

Con la licencia válida, el binario abortaba al cargar el `.engine` con
`*** buffer overflow detected ***`. Con `PYTHONFAULTHANDLER=1` se vio una recursión de
imports: `site-packages/tensorrt/tensorrt/tensorrt/…/__init__.py`. Había dos symlinks que se
apuntaban a sí mismos, de root, del 21/9:

    /usr/lib/python3.10/dist-packages/tensorrt/tensorrt -> (el mismo directorio)
    /usr/lib/python3.10/dist-packages/tensorrt-10.3.0.dist-info/tensorrt-10.3.0.dist-info -> (ídem)

`__init__.py` hace `from .tensorrt import *`, y el buscador de imports prefiere un directorio
con `__init__.py` antes que `tensorrt.so`. **Desde fuentes también recursaba**, pero CPython
frenaba en el nivel 40 —el máximo de symlinks que Linux sigue al resolver una ruta— y cargaba
41 copias del paquete; compilado abortaba antes. Es el patrón de un `ln -s` repetido sobre un
destino que ya existe. Nuestros scripts no los crearon: `venv-setup.sh` usa `ln -sfn` y
escribe en el venv. El usuario los borró con `sudo rm`; ahora `tensorrt` carga dos módulos.
**En la Jetson de la planta conviene mirar que no estén:**

    find /usr/lib/python3.10/dist-packages/tensorrt* -maxdepth 1 -type l

### 5. La potencia sale 0 W, también desde fuentes — maquinaria

En JetPack 6 el INA3221 no publica `power1_input`, que es lo que busca
`system/system_monitor.py:91`. Publica tensión y corriente por canal: `VDD_IN` en
`/sys/bus/i2c/drivers/ina3221/1-0040/hwmon/hwmon2/in1_input` (mV) y `curr1_input` (mA). Medido
en reposo: 5080 mV × 1840 mA ≈ 9,3 W. No es del binario.

### 6. El aviso «different models of devices» sigue saliendo — proyecto

TensorRT lo imprime al abrir `pellet_20260623_fp16.engine`, compilado y desde fuentes. La
reexportación de la ronda 3 no lo sacó. Ver «Arreglos pendientes».

### 7. La seguridad del modo acelerado

En acelerado, **cualquier `.py` suelto que el proceso importe** —yaml, numpy, torch— puede
reemplazar funciones de los módulos compilados en `sys.modules`. Compilar `cryptography`
cerraba sólo la puerta más obvia, y por eso perderla cuesta menos de lo que parece. El
canario sube el costo del ataque fácil —vaciar `Ed25519PublicKey.verify` en
`site-packages`— pero no cierra el parcheo de nuestros módulos desde otro `.py` suelto, que
pide saber qué parchear. El standalone tiene la misma puerta con `pypylon`. El modelo de
amenaza sigue siendo el de `CLAUDE.md`: encarecer la copia casual, no frenar a alguien
decidido con acceso de administrador al equipo.

## El cierre colgado — resuelto, eran dos causas

**Qué pasaba.** Con SIGTERM —como lo cierra un servicio o un `kill`— un `QThread` no terminaba
dentro de los 5 s de `_THREAD_STOP_TIMEOUT_MS` (`[Main] Un hilo no cerró en 5000 ms.`). Si el
hilo seguía corriendo cuando Python destruía el objeto, Qt imprimía `QThread: Destroyed while
thread is still running` y abortaba (`qFatal`, exit 134, `signal 6` en
`/var/log/apport.log`). Antes del arreglo:

| | Corridas | Trabadas | Abortaron |
|---|---|---|---|
| Compilado, inferencia, SIGTERM | 18 | 14 | 9 |
| Compilado, sin pipeline (cámara st_gige sin conectar), SIGTERM | 6 | 6 | 5 |
| Desde fuentes, inferencia, SIGTERM | 6 | 0 | 0 |

### Causa 1: el handler de la señal cerraba con el lock del config tomado (sólo compilado)

`main.py` instalaba `signal.signal(SIGTERM, lambda *_: app.quit())`. Tres hechos juntos:

1. **Python corre el handler entre dos instrucciones cualesquiera del hilo principal.** Desde
   fuentes eso pasa en la lambda del timer «al vacío», sin nada tomado. **Compilado, esa
   lambda es C** y no corre el bucle de evaluación: el handler corre en la primera instrucción
   *no compilada* del hilo principal, que casi siempre es el `copy.deepcopy` de
   `ConfigManager.get()` **con su `RLock` tomado**. Medido con un build instrumentado: 5 de 5
   veces el handler corrió con `ConfigManager._lock._is_owned() == True`.
2. **En Qt 6 `quit()` emite `aboutToQuit` en el acto** (comprobado con PySide6 6.8: el slot
   corre antes de que `quit()` vuelva). `stop()` corría anidado adentro del handler, adentro
   del `deepcopy`, con el lock en la mano.
3. **`stop()` espera a los hilos, y los hilos piden el config.** El de inferencia quedaba en
   `_read_roi_px()` → `config.get()`; el Modbus, igual (eso eran los otros 5 s del `join`).
   Deadlock hasta el timeout, y después aborto si el hilo seguía vivo al destruirse.

**Arreglo** (`main.py`, `_build_interrupt_handler()`): el handler no cierra, agenda el cierre
con `QTimer.singleShot(0, app.quit)`. `stop()` corre desde el event loop, con el stack limpio.
Con su test en `test/test_main.py`.

### Causa 2: sin cámara, `connect()` no veía la interrupción (también desde fuentes)

Con la cámara desconectada el hilo de captura vive adentro de `StDriver.connect()`, que espera
hasta 20 s (`_CONNECT_TIMEOUT_S`) a que el hilo de `stapipy` confirme, y entre reintentos ese
hilo dormía `time.sleep(5)`. Ninguna de las dos esperas se enteraba del `requestInterruption()`
del `QThread`: un SIGTERM al principio de un intento dejaba el hilo vivo hasta 20 s, el `wait`
se rendía y Qt abortaba. Desde fuentes pasaba igual.

**Arreglo:** `AbstractCameraDriver.interrupt()` —nuevo, no abstracto, no-op por defecto—, que
`StDriver` implementa con un `threading.Event` sobre el que esperan `connect()` y los
reintentos; `CaptureThread.requestInterruption()` lo llama. Es terminal: un driver
interrumpido no vuelve a conectar. Con tests en `test/camera/test_capture_thread.py` y
`test/tools/camera/test_st_driver.py`; sin el arreglo, el de captura reproduce el aborto.

### Verificación, con el binario de `build.py` en el entregable

| | Corridas | Trabadas | Abortaron | Cierre |
|---|---|---|---|---|
| Inferencia (mock + licencia), SIGTERM | 15 | 0 | 0 | 1,9 – 2,7 s |
| Sin cámara, SIGTERM 1, 4 y 7 s después de instalado el handler | 3 | 0 | 0 | 1,3 – 2,0 s |
| SIGINT (Ctrl+C) | 1 | 0 | 0 | — |
| Desde fuentes, inferencia, SIGTERM | 2 | 0 | 0 | 2,0 – 2,5 s |

### Cómo se encontró, para la próxima

Faulthandler no ve los frames compilados, `gdb` 12 se cae con los símbolos de CUDA y bajo
`gdb` la falla no aparecía, y `py-spy --native` no muestrea un proceso de Nuitka. Lo que
funcionó fue un **build de laboratorio desde una copia del código** con marcas de tiempo en
un `deque` (sin I/O que cambie los tiempos) en cada etapa del bucle del motor y de `stop()`,
volcadas al final: la última marca del hilo trabado dijo en qué línea estaba. Se descartaron
antes, con pruebas: que `QThread.wait()` retenga el GIL compilado, que el slot de
`result_ready` corra en otro hilo, el gate del anotado, y apagar HTTP, Modbus, InfluxDB, GPIO
y la óptica.

### Lo que quedó, menor

- **Un SIGTERM durante el arranque mata el proceso sin `stop()`.** Los handlers se instalan
  después de `application.start()`, ~5 s después de lanzar (medido con `SigCgt` en
  `/proc/<pid>/status`). Desde fuentes es igual. Es el default de Linux; un servicio que se
  detiene apenas arrancó deja la cámara para que la libere su propio timeout de heartbeat.
- **Sin cámara, `stapipy` congela la app unos 3 s en cada búsqueda.** El hilo principal estuvo
  3,6 s sin correr entre el log de la versión y la instalación del handler, que coincide con
  la enumeración. Todo indica que la extensión no suelta el GIL mientras busca. No se midió el
  efecto sobre el latido del Modbus; vale mirarlo, porque el PLC lo vería como un enlace que
  se cae cada 8 s mientras la cámara falta.

**Cómo reproducir los cierres**, como prueba de regresión, con la carpeta de prueba y la
licencia instaladas:

    R=~/release_test/"Detección de calidad de pellet-1.0.0"
    export SIP_DATA_DIR="$R/installation" PYTHONNOUSERSITE=1
    export PYTHONPATH="$R/program/site-packages"
    for i in 1 2 3 4 5; do
        LOG=/tmp/ciclo-$i.log
        "$R/program/main.bin" --headless > $LOG 2>&1 &     # sin `cd ... &&`: $! tiene que ser main.bin
        PID=$!
        until grep -q "Hilo iniciado. Etapas" $LOG; do sleep 1; done
        sleep 10
        START=$(date +%s.%N); kill -TERM $PID; wait $PID; RC=$?
        echo "$i: exit $RC, $(echo "$(date +%s.%N) - $START" | bc) s, \
    no_cerró=$(grep -c 'no cerró' $LOG), qthread=$(grep -c 'QThread: Destroyed' $LOG)"
        sleep 3
    done

Desde fuentes es lo mismo con `.venv/bin/python main.py --headless` desde la raíz del repo.

## Mediciones (para `build/results.csv`)

| Campo | Valor |
|---|---|
| fecha / commit | 2026-10-09 / `5b40f05` |
| Nuitka | 4.2.2, Python 3.10.12 (Debian), gcc 11.4, ccache |
| modo / `--jobs` / potencia | acelerado / default (6 núcleos) / 20W |
| build | 102 s (1,7 min); con `cryptography` compilada eran 137 s |
| pico de memoria | 251 MB el proceso hijo más grande; `MemAvailable` mínima 2,3 GB; swap sin tocar |
| `main.bin` | 11,1 MB |
| `site-packages` | 2852 MB según `make_release.py` (torch 1472, PySide6 565, pypylon 186, polars runtime 169, en MiB de `du`) |
| carpeta | 2872 MB |
| `.tar.gz` | 1276 MB; `make_release.py` tarda 8 min 44 s, casi todo comprimiendo |
| ciclo de inferencia | mediana 50 ms (p10 41, p90 65) con el entregable de los scripts |
| standalone | nunca terminó; sin números (ver «¿Y el standalone?») |

## Paso 5 — llevarlo a la maquinaria (hecho y commiteado)

Todo es del template —cualquier fork en una Jetson tiene el mismo problema—, en commits
separados de los del proyecto y anotado en la sección B de `docs/template_divergences.md`.
La suite pasa entera (1799 tests). Lo que quedó implementado, punto por punto:

- **`build.py`:** el modo acelerado como opción, con las banderas de «El comando que anduvo».
  `cryptography` **no** va en la lista de lo que se compila, con el motivo al lado (hallazgo
  3), para que nadie la agregue «por seguridad». El standalone sigue siendo el de Windows.
- **`system/paths.py`:** `_app_dir()` según `__compiled__.standalone` (hallazgo 2).
- **`system/license/verify.py`:** el canario. Al verificar, una firma conocida como mala
  tiene que rechazarse; si pasa, el verificador fue reemplazado y la licencia se da por
  inválida, con un motivo que lo diga. Con su test.
- **`make_release.py`:** la forma acelerada —`main.bin`, `ui/styles`, `ui/icons` y
  `program/site-packages/` sin lo de build ni de test—, los lanzadores exportando
  `PYTHONPATH` y `PYTHONNOUSERSITE`, y `Verificar camara.sh` mirando ese `site-packages`.
  `_LOOSE_PACKAGES` deja de ser un caso aparte: en este modo todo lo de terceros va suelto.
- **`system/system_monitor.py`:** la potencia desde `in1_input × curr1_input` (hallazgo 5).
- **`build/requirements.txt`:** Nuitka 4.2.2 declarado.
- **`build/README.md`:** la Jetson usa la forma acelerada, y por qué.
- **`setup-jetson.sh`: no cambia.** `libpython` va estática.
- Opcional, **no hecho**: que `verify_env.py` detecte un symlink de un paquete de sistema
  que se apunta a sí mismo (hallazgo 4).

Detalles de lo implementado que no estaban en la lista:

- La forma la decide una sola constante, `_ACCELERATED = not _IS_WINDOWS` en `build.py`, y
  `make_release.py` la lee de ahí junto con `_ICON` y `_DATA_DIRS`.
  `--no-deployment-flag=excluded-module-usage` quedó sólo en el standalone.
- `make_release.py` saca lo de build y test **por la metadata de cada distribución**
  (`_BUILD_ONLY_DISTRIBUTIONS`), y retiene una que otra distribución declara como
  dependencia, salvo las dependencias de un extra (`pytest; extra == "test"`).
- `Verificar camara.sh` encuentra `stapipy` como `.so` suelto: en Linux no es una carpeta,
  y el script anterior lo daba por faltante.
- El canario usa el vector 1 de la RFC 8032, y cualquier excepción del verificador cuenta
  como «no responde como Ed25519».
- Las pruebas de `system_monitor` redirigen también el glob nuevo: si no, en una Jetson
  leían el sensor real.

**De punta a punta con los scripts**, con el venv movido:

| Paso | Resultado |
|---|---|
| `build.py --force` | OK, 99 s, el mismo comando que el probado a mano salvo el deployment flag |
| `make_release.py` | OK. `site-packages` sin `nuitka`, `ordered_set`, `zstandard`, `pytest`, `_pytest`, `py.py`, `pluggy` ni `iniconfig`; `gi` y `tensorrt` como symlinks; permisos de ejecución intactos en el `.tar.gz` |
| `Verificar camara.sh` | OK: stapipy y pypylon encontrados, `ldd` sin faltantes, `GENICAM_GENTL64_PATH` puesta |
| 4.2 sin `SIP_DATA_DIR` | OK: busca `program/config.yaml` y loguea en `program/data/logs` |
| 4.1 / 4.3 / 4.6 por el lanzador | OK: nada de `.venv`, `~/.local` ni `dist-packages` salvo `gi`; licencia `absent`; reg 1 = `0x0010`, reg 2 = `0x0012`, latido avanzando |
| Canario | OK: con `from_public_bytes` de la copia suelta reemplazado por uno que no verifica, «La verificación de firmas no pasa su autocontrol» |
| 4.7 | OK: licencia válida, `.engine` en CUDA, mediana 50 ms |
| Cierre | Antes de los arreglos, 3 de 6 trabadas y 2 abortadas; después, 0 de 15 (ver «El cierre colgado») |

## Los commits para el PR

Seis de maquinaria, en este orden, sobre `5b40f05`. Cada uno lleva sus tests y se sostiene
solo; el orden importa porque el build nombra al autocontrol del verificador:

| Commit | Qué |
|---|---|
| `8acb15f` | Autocontrol del verificador de licencias (`verify.py`) |
| `9e3a8d7` | `APP_DIR` del build acelerado (`paths.py`) |
| `6fec65d` | Build acelerado en Linux (`build.py`, `make_release.py`, `requirements.txt`, README) |
| `a5bc36e` | Potencia en JetPack 6 (`system_monitor.py`) |
| `880a14a` | El handler de señales agenda el cierre (`main.py`, fuera del bloque del fork) |
| `0f74fec` | `interrupt()` en el contrato del driver de cámara |

Después va uno del proyecto, con este plan, `CLAUDE.md` y `docs/template_divergences.md`,
que **no** viaja al template. Para el PR, una rama del template con esos seis
`cherry-pick` en orden; `main.py` es el único que puede conflictuar, y el hunk es sólo el
del handler.

## Paso 6 — medir y anotar

`build/results.csv` con la tabla de «Mediciones», y la corrida por scripts del paso 5.

## Reglas para la sesión (siguen vigentes)

- **Un paso por vez, y se verifica antes de seguir.** Reportar `paso -> OK / FAIL + la línea
  exacta`.
- **Los commits de maquinaria van separados de los del proyecto**, para que el PR al template
  salga limpio.
- **sudo lo corre el usuario.** La sesión no tiene la contraseña: da el comando exacto y lee
  la salida.
- **Lo largo se larga en segundo plano** con log a archivo, y se mira el log.
- **Las pruebas del binario se hacen con el venv movido** (`mv .venv .venv.off`), para que una
  dependencia escondida falle fuerte. Al terminar, `mv .venv.off .venv`.

---

## Arreglos pendientes, aparte del build

### Del proyecto

- **La cámara.** Conectar la STC-MCS312POE y correr
  `setup/jetson/setup-jetson.sh --camera-iface=<la NIC de la cámara>` para MTU 9000. Ojo:
  `enP1p1s0` es la LAN, no la cámara. Después, los pasos 3 a 6 del `README.md`: frames,
  exposición, umbral de fondo oscuro, rectángulo de cinta y referencia de la óptica. Recién
  con frames reales se sabe el tiempo de inferencia de verdad: con cientos de partículas
  pesa el postproceso de máscaras, no el motor. Y es el paso 4.8 del binario.
- **El `.engine` en esta Orin NX.** El aviso «different models of devices» **sigue saliendo**
  (hallazgo 6): hay que reexportarlo en esta Jetson con ultralytics 8.4.90 y confirmar que el
  aviso desaparece. Si la planta tiene otra Jetson, se reexporta allá, o se cambia el pin de
  `setup/jetson/constraints.txt` en el mismo commit.
- **`.env` en la Jetson** con `INFLUXDB_TOKEN`, desde `.env.example`. Sin eso InfluxDB queda
  caído y el registro 2 no prende su bit.
- **RTSP:** el codec ya es `h264_hw`, pero `video.rtsp.enabled` sigue en `false`.
  Prenderlo es decisión del usuario, y decide si el paso 4.10 bloquea la entrega.
- **La versión.** Sigue en 1.0.0 y este trabajo cambió lo que hace el equipo: el modelo abre el
  motor al cargar y el RTSP codifica por hardware. Por la regla de `system/version.py` es
  1.1.0, en el commit que cierre la próxima entrega. Lo decide el usuario.
- **La licencia de la planta** se pide desde la pestaña de licencia **del equipo de la
  planta**, no de esta Jetson: la solicitud lleva la huella de la máquina que la genera. La
  que hay en `build/license/` es de esta Jetson y de prueba.

### De maquinaria (PR al template)

- **El modo acelerado en la Jetson**: los cambios del paso 5.
- **El cierre**: los dos arreglos de «El cierre colgado» —el handler que agenda el cierre en
  `main.py` y `interrupt()` en el contrato del driver—, commiteados. Quedan dos menores
  anotados ahí: un SIGTERM durante el arranque y el congelamiento de `stapipy` sin cámara.
- **`test_st_driver.py`: 15 errores en una PC sin stapipy** (18 con los 3 de `TestInterrupt`,
  que usan el mismo fixture). Los tests parchean `st_driver.st`,
  que no existe cuando el import falló. Probable arreglo: `monkeypatch.setattr(...,
  raising=False)` o un doble del módulo. En la Jetson pasan porque stapipy está.
- **`--python-flag=no_docstrings`**: en modo acelerado sólo toca lo nuestro y dejó de ser un
  problema. Si se vuelve al standalone, hacerlo opcional.
- **`manual_test/ui/config.yaml`** no declara `video.http.stream_names` y avisa al arrancar.
  Es cosmético.

### Mejoras que valen la pena, sin urgencia

- **MJPEG por hardware.** El stream HTTP —el que está prendido— codifica JPEG por CPU con
  OpenCV. La Jetson tiene NVJPG (`nvjpegenc` en GStreamer). Es un cambio de
  `http_server.py`, maquinaria.
- **El VIC para el preprocesado.** El escalado del RTSP podría ir en `nvvidconv` en vez de
  `videoscale`, y la corrección de lente y el redimensionado del preprocessor por VPI
  (`python3-vpi3`, enlazable al venv como `gi`). Todo maquinaria, y recién después del
  loader nativo tiene sentido llevar también el letterbox del modelo a la GPU.
- **El loader nativo de TensorRT** (`CLAUDE.md`, «Qué todavía no existe»): sacaría torch,
  torchvision y ultralytics del entregable —1,5 GB de los 2,8— y abriría los pesos cifrados.
- **PySide6:** subir de 6.8.0.2 sólo cuando haya wheel aarch64 para el glibc de JetPack 6,
  y en la PC y la Jetson a la vez.
