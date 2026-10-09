# Plan: compilar el entregable en la Jetson, y lo que queda por arreglar

Para una sesión nueva que arranca en la Jetson. Leer `CLAUDE.md` primero; este plan no
repite el mapa ni las decisiones, sólo dice dónde estamos, qué se decidió, qué falta y en
qué orden.

## Dónde estamos (2026-10-09)

**El equipo de build es también el de prueba.** Seeed reComputer Industrial J201 con
Jetson Orin NX 8 GB, L4T R36.4.0 (JetPack 6.1), TensorRT 10.3, CUDA 12.6. Usuario `iea`, repo
en `/home/iea/Documents/sip_27084`, rama `migration/belt-monitor-27084`. El fork está en
`b514ead`, que ya trae el template 0.6.0 (PR #35).

**Lo que está verificado en esta Jetson, corriendo desde fuentes:**

- `setup/jetson/setup-jetson.sh` y `setup/venv-setup/venv-setup.sh --force` limpios;
  `verify_env.py` da `RESULT: environment OK`.
- El venv está aislado (sin `--system-site-packages`); `gi` y `tensorrt` son symlinks a apt.
- torch `2.5.0a0+872d972e41.nv24.08`, torchvision 0.20.0, ultralytics 8.4.90, PySide6
  6.8.0.2, OpenCV headless 4.8, gpiod 2.5.
- El `.engine` carga en CUDA y abre adentro de `load()`; la primera inferencia tarda ~4.5 s
  (TensorRT abriendo el motor). Con frames de ruido 2048×1536 a 20 W: mediana 24.8 ms.
- Modbus TCP en el 502 sin root, leído con FC03. `h264_hw` entrega frames por RTSP.
- La suite pasa entera en la Jetson.

**Lo que se aprendió intentando el standalone:** con 8 GB de RAM el `--standalone` de hoy
es casi imposible de terminar. No es un problema de opciones: Nuitka traduce a C cada `.py`
que alcanza, y desde `main.py` alcanza torch, torchvision, ultralytics y todo lo que ellos
importan —sympy, networkx, jinja2, polars, matplotlib, pandas si aparece—, miles de módulos
que gcc compila uno por uno. *(Completar con lo medido: hasta dónde llegó, cuánto tardó, el
pico de memoria y con qué swap y `--jobs`.)*

**Lo que nunca se probó:** un binario de Linux que arranque. Y ningún frame real de la
cámara pasó por el modelo: la cámara no estaba conectada.

## La decisión: compilar lo nuestro, y que las librerías corran desde un venv

### ¿Hace falta compilar PyTorch y las demás librerías?

**No.** Compilarlas no da nada que este entregable necesite:

- **Nuitka no compila lo pesado de ninguna librería.** Las extensiones nativas —el `.so`
  de torch, CUDA, TensorRT, OpenCV, Qt— se **copian** tal cual en cualquier modo. Lo único
  que Nuitka traduce a C es la capa de Python de esas librerías, y ahí no se gana
  velocidad: el tiempo de inferencia está en CUDA y en TensorRT, no en el Python que los
  llama.
- **Tampoco protege nada.** torch, ultralytics y Qt son públicos. Lo que hay que proteger
  es nuestro: la licencia (`system/license/`), el pipeline, las métricas. Eso sí se compila.
- **El standalone igual no es autosuficiente en una Jetson.** El equipo de la planta
  necesita JetPack, el driver, `libcuda`, TensorRT y GStreamer de apt, y pasa por
  `setup-jetson.sh` y el SDK de la cámara antes de correr nada. Copiar al binario
  bibliotecas que **tienen** que venir del sistema era justamente el riesgo del paso 3 del
  plan anterior (CUDA, TensorRT y los plugins de GStreamer con otra versión que la del
  equipo).

### ¿Pueden estar en un venv?

**Sí, y es la forma recomendada.** Nuitka tiene un modo para esto, el **acelerado** (sin
`--standalone`): compila `main.py` y los paquetes que se le indican, y todo lo demás lo
importa en runtime del intérprete y del `site-packages` que encuentre. Es la forma que el
`build/README.md` ya describe como «la más barata, y conviene medirla antes de asumir
standalone».

| | Standalone (hoy) | **Acelerado + site-packages (propuesto)** |
|---|---|---|
| Qué se compila | todo lo que `main.py` alcanza | `main.py`, `system/`, `tools/`, `ui/` y `cryptography` |
| Build en 8 GB | horas, OOM | minutos, esperable sin swap (hay que medirlo) |
| torch, ultralytics, Qt | traducidos a C y copiados | la misma copia que pasó `verify_env.py`, sin tocar |
| CUDA, TensorRT, GStreamer | Nuitka decide qué copia | del sistema, como desde fuentes |
| Datos de paquete (`cfg/default.yaml` de ultralytics) | hay que declararlos uno por uno | están donde ultralytics los busca |
| `--python-flag=no_docstrings` | rompe librerías de terceros | sólo afecta a lo nuestro |
| Python en el destino | no | el `python3.10` de JetPack, que siempre está |

**De dónde sale el venv en la planta.** Recomendado: **el `site-packages` del venv
verificado viaja adentro del entregable** (`program/site-packages/`), copiado y no
compilado —es el mismo mecanismo que `make_release.py` ya usa para `stapipy` y `pypylon`,
ampliado—. La planta no necesita internet ni pip, y corre exactamente los bytes que se
probaron acá. Los symlinks de `gi` y `tensorrt` viajan como symlinks y resuelven contra el
apt del equipo de la planta, que tiene el mismo JetPack. La alternativa —correr
`venv-setup.sh` en la planta— necesita internet, `packages/` y resuelve versiones nuevas
de lo que no está fijado: queda como camino de desarrollo, no de entrega.

**El precio, y cómo se paga.** Lo que está afuera del binario se puede editar con un
editor de texto. Para la medición da igual, pero no para la licencia: si
`cryptography` estuviera suelta, reemplazar su verificador Ed25519 por uno que siempre
diga «válida» sería el ataque de un rato. Por eso **todo lo que toca la verificación de la
licencia va compilado**: `system/license/` y la capa de Python de `cryptography` (su
extensión en Rust sigue siendo un `.so` suelto, y reemplazarla ya es trabajo de días). Los
pesos del modelo no pierden nada: hoy viajan en claro, y el loader nativo de TensorRT
tampoco podría compilar `tensorrt`, que es de apt.

### ¿Hay que probar TensorRT, GStreamer y compañía en el binario compilado?

**Sí, pero como prueba de costura y no como recalificación.** Lo que un binario cambia
respecto de correr desde fuentes son dos cosas: que nuestro código está compilado, y
**de dónde** se cargan las librerías. Con el modo acelerado las librerías se cargan igual
que desde fuentes —el mismo `site-packages`, las mismas bibliotecas del sistema—, así que
su comportamiento ya está verificado; lo que hay que ver es que el binario **las
encuentre**. Una corrida por componente alcanza:

| Componente | Cómo llega al binario | Qué se prueba | ¿Bloquea la entrega? |
|---|---|---|---|
| Lo nuestro (`system`, `tools`, `ui`) | compilado | arranque, rutas, licencia, UI, Modbus — el grueso del paso 4 | sí |
| `cryptography` | Python compilado, `.so` suelto | sale con la prueba de licencia | sí |
| PySide6 | suelto; los slots compilados necesitan el plugin `pyside6` de Nuitka | la UI abre y responde a clicks y señales | sí |
| torch, ultralytics, TensorRT | sueltos / apt | **una** inferencia con el `.engine`, tiempo comparable a los 24.8 ms | sí: es el producto |
| stapipy | suelto, como hoy | frames reales de la cámara | sí |
| gpiod | suelto | `hardware_available` en diagnóstico | sí |
| GStreamer (`gi`) | apt, por symlink | un cliente RTSP recibe frames con cámara `mock` | sólo si la planta va a prender RTSP |

Con standalone la respuesta sería otra: ahí sí habría que recalificar cada una, porque
Nuitka decide qué bibliotecas copia y desde dónde se cargan los plugins.

**La inferencia compilada necesita una licencia de esta máquina**, porque sin licencia
ningún pipeline arranca. Para no depender del repositorio de firma en una prueba, se hace
un **build de laboratorio** con una clave descartable (paso 0.4). Ese binario no sale
nunca del laboratorio.

## Reglas para la sesión

- **Un paso por vez, y se verifica antes de seguir.** Reportar cada paso como
  `paso -> OK / FAIL + la línea exacta`.
- **Primero se prueba a mano, después se toca la maquinaria.** Pasar a modo acelerado
  cambia `build.py`, `make_release.py` y casi seguro `system/paths.py`, que son del
  template. Los pasos 1 a 4 se hacen **sin editarlos**: con el comando de `--dry-run`
  ajustado a mano y una carpeta armada a mano. Recién con eso andando se proponen los
  cambios (paso 5), se dice que son del template y se anotan en la sección B de
  `docs/template_divergences.md`.
- **Los commits de maquinaria van separados de los del proyecto**, para que el PR al
  template salga limpio.
- **sudo lo corre el usuario.** La sesión no tiene la contraseña: da el comando exacto y
  lee la salida.
- **Lo largo se larga en segundo plano** con log a archivo, y se mira el log.

## Paso 0 — antes de compilar

1. **`git pull`**, y confirmar que `git log -1` está en `b514ead` o después.
2. **Nuitka**, en el venv y respetando los pines:

       .venv/bin/python -m pip install -c setup/jetson/constraints.txt nuitka ordered-set zstandard

   Anotar la versión. Dónde se declara (`build/requirements.txt`) es cambio de maquinaria.
3. **Modo de potencia máximo** mientras compila: `sudo nvpmodel -q`; si no está en el
   máximo, `sudo nvpmodel -m 0` y `sudo jetson_clocks`. El swap ya no debería hacer falta;
   si el paso 1 muere con `Killed`, recién ahí `fallocate` de 16 GB como en el plan anterior.
4. **Las claves: este build es de laboratorio.** Preguntarle al usuario si el repositorio
   de firma está disponible. Si no:
   - Generar un par Ed25519 descartable con `cryptography`.
   - Escribir un `system/license/_public_key.py` de prueba con la interfaz que lee
     `public_key.py`: `has_any_key()` y `get_public_key(key_id)`, con la pública en claro
     (el enmascarado del repositorio de firma no hace falta para probar).
   - Firmar una licencia para la huella de **esta** Jetson igual que
     `test/license/conftest.py` (`schema.build_token(payload, private_key.sign(payload))`),
     con el payload de `python -m system.license request`.
   - La privada queda en el scratchpad y se borra al terminar. **Un binario con esa clave
     acepta licencias firmadas por cualquiera que tenga la privada descartable: no se
     empaqueta para entregar.** Se compila con `--out build/out-lab`.

## Paso 1 — compilar a mano en modo acelerado

    .venv/bin/python build/build.py --dry-run

Copiar el comando que imprime y cambiarle, a mano:

- **sacar** `--standalone` y los `--include-data-dir` (en modo acelerado no se copian);
- **agregar** `--follow-import-to=system --follow-import-to=tools --follow-import-to=ui
  --follow-import-to=cryptography`. Sin `--standalone` Nuitka no sigue imports por
  defecto, así que sin esto compilaría sólo `main.py`;
- **agregar** `--include-package=system --include-package=tools --include-package=ui`:
  `_public_key` y `_model_key` se importan adentro de un `try/except ImportError`, y si el
  análisis no los sigue el binario los buscaría como archivo en runtime —sin encontrarlos,
  o encontrando uno que alguien dejó ahí—;
- **dejar** `--enable-plugin=pyside6`: los slots de `ui/` son funciones compiladas;
- **agregar** `--python-flag=no_site`: sin `site`, el intérprete no suma
  `/usr/lib/python3/dist-packages` (el matplotlib 3.5 y el scipy 1.8 de JetPack) ni
  `~/.local`, que es por lo que el venv se armó aislado;
- **agregar** `--output-dir=build/out-lab --report=build/out-lab/compilation-report.xml`.

Correrlo en segundo plano, midiendo tiempo y memoria:

    nohup /usr/bin/time -v <el comando> > build/build-lab.log 2>&1 &

Anotar `Elapsed`, `Maximum resident set size` y si hubo errores.

## Paso 2 — mirar qué salió

1. `ldd build/out-lab/main.bin | grep -i python`: ¿enlaza `libpython3.10.so` o la trae
   estática? Si es dinámica, el equipo de la planta necesita el paquete `libpython3.10`
   (verificar con `dpkg -s libpython3.10`; si `setup-jetson.sh` no lo garantiza, es
   cambio de maquinaria).
2. En el `compilation-report.xml`: están **todos** los `system.*`, `tools.*`, `ui.*`,
   `system.license._public_key` y `cryptography.*`; **no** están torch, torchvision ni
   ultralytics.

## Paso 3 — armar a mano la carpeta de prueba

Fuera del repo, con la misma forma que va a tener el entregable:

    ~/release_test/
        program/
            main.bin
            ui/styles/   ui/icons/              copiados del repo
            site-packages/                      cp -a de .venv/lib/python3.10/site-packages
        installation/                           config.yaml, register_map.yaml, models/
        lanzar.sh                               exporta SIP_DATA_DIR, PYTHONPATH y
                                                PYTHONNOUSERSITE=1, y corre main.bin

- Del `site-packages` copiado sacar lo que es sólo de build o de test (nuitka, pytest,
  ordered-set) y anotar el tamaño antes y después.
- `ls program/site-packages/*.pth`: con `PYTHONPATH` los `.pth` **no** se procesan. Si
  alguno hace algo más que `distutils-precedence`, anotarlo: es lo primero a mirar si un
  import falla.
- **Mover el venv del repo** mientras duran las pruebas (`mv .venv .venv.off`): cualquier
  dependencia escondida del binario con el venv de build falla fuerte en vez de pasar.

## Paso 4 — probarlo, de lo barato a lo caro

1. **Arranque sin pantalla** (`--headless`). Con el proceso vivo:

       grep -E "\.venv|/usr/lib/python3/dist-packages|\.local" /proc/$(pgrep -f main.bin)/maps

   No tiene que salir nada, y `program/site-packages` sí tiene que aparecer.
2. **Las rutas.** Riesgo conocido: `paths._app_dir()` usa `sys.executable`, que en modo
   acelerado es probable que apunte al intérprete de build y no al `main.bin`. Si es así,
   la app busca los QSS y los iconos en el lugar equivocado (y sin `SIP_DATA_DIR`, también
   el config). Confirmar en el log qué `APP_DIR` resolvió. Si está mal, el arreglo va a
   `paths.py` (maquinaria): candidato `__compiled__.containing_dir` si la versión de Nuitka
   lo trae, o `sys.argv[0]` primero.
3. **Licencia sin archivo:** estado `absent`, modo de puesta en marcha, chip rojo y cartel
   al abrir, ningún pipeline arranca, la pestaña genera la solicitud.
4. **Que no se pueda inyectar una clave.** Dejar un `program/system/license/_public_key.py`
   con otra clave y confirmar que el binario lo ignora y sigue usando la compilada. Si lo
   usa, el modo acelerado no sirve para entregar tal como está: parar y reportar.
5. **Con ventana:** la UI abre, se navega, los botones y señales responden.
6. **Modbus:** el mismo FC03 al 502 que desde fuentes.
7. **Inferencia con la licencia de laboratorio:** el pipeline arranca, el `.engine` abre en
   CUDA, y la mediana con frames sintéticos está cerca de los 24.8 ms medidos desde fuentes.
8. **Cámara real**, si ya está conectada (ver «Arreglos pendientes»): frames por stapipy.
9. **GPIO:** `hardware_available` en la pestaña de diagnóstico.
10. **RTSP:** sólo si la planta lo va a prender. Config con `video.rtsp.enabled: true`,
    cámara `mock`, un cliente recibe frames.

Al terminar, `mv .venv.off .venv`, y borrar la clave descartable y el `_public_key.py` de
prueba si quedó alguno.

## Paso 5 — llevarlo a la maquinaria

Con los números del paso 4 en la mano, proponerle al usuario los cambios. Todos son del
template —cualquier fork en una Jetson tiene el mismo problema—, en commits separados y
anotados en la sección B de `docs/template_divergences.md`:

- **`build.py`:** el modo acelerado como opción de la plataforma o del fork, con las
  banderas del paso 1. Una lista de terceros que se compilan por seguridad
  (`cryptography`), con el motivo al lado. `--python-flag=no_site` en ese modo.
- **`make_release.py`:** la forma acelerada del entregable —`main.bin`, `ui/styles`,
  `ui/icons` y `program/site-packages/` sin lo de build—, los lanzadores exportando
  `PYTHONPATH` y `PYTHONNOUSERSITE`, y `Verificar camara.sh` mirando ese `site-packages`.
  `_LOOSE_PACKAGES` deja de ser un caso aparte: en este modo todo lo de terceros va suelto.
- **`paths.py`:** el `APP_DIR` del modo acelerado, si el paso 4.2 confirmó el problema.
- **`setup-jetson.sh`:** `libpython3.10`, si el paso 2 mostró que hace falta.
- **`build/requirements.txt`:** Nuitka declarado.
- **`build/README.md`:** la Jetson pasa a usar la forma acelerada, y por qué.

Implementado eso, repetir de punta a punta con los scripts y no a mano:
`build.py` → `make_release.py` → descomprimir el `.tar.gz` en `~/release_test` → paso 4.
Ese es el entregable que vale.

## Paso 6 — medir y anotar

En `build/results.csv`: fecha, commit, versión de Nuitka, modo, `--jobs`, tiempo de build,
pico de memoria, tamaño de `main.bin`, de `site-packages`, de la carpeta y del `.tar.gz`, y
las opciones que hubo que agregar. Anotar también el intento standalone, con lo que se
haya medido, para que la decisión quede con su número.

## Si el modo acelerado no sirve

En el orden en que conviene intentarlo:

1. **Standalone, con lo pesado suelto.** `--standalone` con
   `--nofollow-import-to=torch,torchvision,ultralytics,...` y esos paquetes copiados en
   `program/` como hoy se copia `stapipy` (`--no-deployment-flag=excluded-module-usage` ya
   está para que el import ande). El riesgo: un standalone sólo trae los módulos de la
   librería estándar que alcanza lo compilado, y torch y ultralytics usan otros. Faltan en
   runtime, de a uno, como `ModuleNotFoundError`, y cada uno es un `--include-module` más.
2. **El loader nativo de TensorRT** de «Qué todavía no existe» en `CLAUDE.md`: saca torch,
   torchvision y ultralytics del entregable por completo y abre los pesos cifrados. Es
   cambio del proyecto (`yolo_seg_model.py` o un modelo nuevo) con su test de paridad
   contra ultralytics. Achica cualquiera de los dos modos, pero no reemplaza la decisión:
   `tensorrt` sigue siendo de apt.

---

## Arreglos pendientes, aparte del build

### Del proyecto

- **La cámara.** Conectar la STC-MCS312POE y correr
  `setup/jetson/setup-jetson.sh --camera-iface=<la NIC de la cámara>` para MTU 9000. Ojo:
  `enP1p1s0` es la LAN, no la cámara. Después, los pasos 3 a 6 del `README.md`: frames,
  exposición, umbral de fondo oscuro, rectángulo de cinta y referencia de la óptica.
  Recién con frames reales se sabe el tiempo de inferencia de verdad: con cientos de
  partículas pesa el postproceso de máscaras, no el motor.
- **El `.engine` en esta Orin NX.** La ronda 3 lo reexportó para que TensorRT no advierta
  «different models of devices». Confirmar en el log del arranque que ese aviso no sale.
  Si la planta tiene otra Jetson, se reexporta allá, con ultralytics 8.4.90 o cambiando el
  pin de `setup/jetson/constraints.txt` en el mismo commit.
- **`.env` en la Jetson** con `INFLUXDB_TOKEN`, desde `.env.example`. Sin eso InfluxDB queda
  caído y el registro 2 no prende su bit.
- **RTSP:** el codec ya es `h264_hw`, pero `video.rtsp.enabled` sigue en `false`.
  Prenderlo es decisión del usuario, y decide si el paso 4.10 bloquea la entrega.
- **La versión.** Sigue en 1.0.0 y este trabajo cambió lo que hace el equipo: el modelo abre
  el motor al cargar y el RTSP codifica por hardware. Por la regla de `system/version.py`
  es 1.1.0, en el commit que cierre la próxima entrega. Lo decide el usuario.
- **La licencia de la planta** se pide desde la pestaña de licencia **del equipo de la
  planta**, no de esta Jetson: la solicitud lleva la huella de la máquina que la genera.

### De maquinaria (PR al template)

- **El modo acelerado en la Jetson**: los cambios del paso 5.
- **`test_st_driver.py`: 15 errores en una PC sin stapipy.** Los tests parchean `st_driver.st`,
  que no existe cuando el import falló. Probable arreglo: `monkeypatch.setattr(...,
  raising=False)` o un doble del módulo. En la Jetson pasan porque stapipy está.
- **`--python-flag=no_docstrings`**: en modo acelerado sólo toca lo nuestro y deja de ser un
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
- **PySide6:** subir de 6.8.0.2 sólo cuando haya wheel aarch64 para el glibc de JetPack 6,
  y en la PC y la Jetson a la vez.
