# Compilación con Nuitka

Las salidas de build no se versionan; sí este archivo, `build.py` y `make_release.py`.

## La forma del entregable

Nuitka compila código Python a C; PySide6, numpy, OpenCV y cualquier framework de
inferencia son extensiones ya compiladas que Nuitka **copia**, no compila. La pregunta no
es "¿se puede compilar la librería X?" sino "¿el entregable la lleva adentro o la busca en
la máquina?". Tres formas:

| Forma | Qué lleva | ¿Python en el destino? |
|---|---|---|
| Acelerada (sin `--standalone`) | sólo el código propio, compilado | **sí**, el venv completo |
| **Standalone** | todo, en una carpeta | no |
| Onefile | todo, en un `.exe` | no, pero descomprime a temp en cada arranque |

**Este proyecto usa standalone.** Onefile queda descartado en la mayoría de los casos por
el tiempo de descompresión en cada arranque, y porque es la forma que más falsos positivos
levanta en los antivirus. La acelerada dejaría un intérprete a mano en el equipo del
cliente; si eso no es un problema para el fork, es la opción más barata —el comando es el
mismo, sacando `--standalone`— y conviene medirla antes de asumir standalone por defecto.

## Cómo se compila

    .venv\Scripts\python.exe build\build.py                   compila
    .venv\Scripts\python.exe build\build.py --dry-run         muestra el comando y no compila
    .venv\Scripts\python.exe build\build.py --out build/out2  otro directorio de salida

**Las opciones viven en `build.py` y no acá**, con el motivo de cada una al lado. La razón
de que sea un script y no un comando para copiar es que una de las opciones no es una
constante: el `.exe` lleva la versión en sus propiedades, y ese número tiene un solo dueño
—`system/version.py`—. Escrito a mano acá sería una cuarta copia y la única que nadie
puede notar desactualizada.

`--dry-run` imprime el comando entero, así que sigue estando a mano para leerlo o para
correrlo con algo cambiado. Y lo que va después de `--` se agrega al final:

    .venv\Scripts\python.exe build\build.py -- --lto=yes

**El directorio de salida tiene que estar vacío**, y el script lo exige antes de arrancar.
Nuitka no pisa los `.c` que dejó una corrida anterior: aborta con un `AssertionError` que
no explica nada, o con un error del backend de Scons. Se corta con el motivo, o se borra
con `--force`.

## Lo que cada fork edita en `build.py`

Seis constantes al principio del archivo, marcadas en el propio docstring:

- `_COMPANY`, `_PRODUCT`, `_DESCRIPTION`, `_ICON` — identidad del programa en las
  propiedades del `.exe`. `_PRODUCT` es un placeholder (`APP_NAME`): conviene que
  coincida con `system.app_name` del `config.yaml`, que es el título de la ventana.
- `_EXCLUDED` — paquetes que **no** entran al ejecutable, vacía por defecto. Un fork que
  usa un framework de inferencia pesado y no lo necesita en el entregable —por ejemplo,
  si exporta su modelo a ONNX Runtime y ya no corre TensorFlow— lo agrega acá. Nuitka
  empaqueta todo lo que `main.py` importa; esto es sólo para lo que el fork sabe que no
  hace falta.

## El entregable

Lo arma `build/make_release.py` a partir del `main.dist` de Nuitka:

    .venv\Scripts\python.exe build\make_release.py

El nombre de la carpeta y de los lanzadores sale de `system.app_name` en el
`config.yaml` que se está empaquetando, así que no hay un segundo nombre que mantener
sincronizado con el título de la ventana. Ver el docstring del script para la forma
completa del entregable, por qué el programa y los datos de la instalación quedan en
carpetas separadas (`program/` e `installation/`), por qué los accesos directos se crean
en el equipo y no viajan adentro del paquete, y qué hace `Verificar camara.cmd`.

## Qué falta medir en cada fork

- El tamaño y el tiempo de build reales de esta instalación, con sus exclusiones. Un
  `results.csv` al lado de este archivo —no versionado por defecto, ver `.gitignore`— es
  un lugar cómodo para llevar esa historia.
- El entregable en una máquina limpia, sin Python y sin el SDK de cámara instalado:
  `Verificar camara.cmd` está para eso.
- Si el equipo de build tiene antivirus corporativo, si compila con una exclusión para el
  directorio de salida, y si el `.exe` final sobrevive sin firma en el equipo de destino.
