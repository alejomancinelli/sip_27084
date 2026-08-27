# Plantilla de proyectos de visión artificial industrial

Captura de una o varias cámaras GigE, inferencia, y publicación del resultado a PLC por
Modbus, a dashboards por telemetría, a video por HTTP/RTSP y a un dataset en disco.

**Cada instalación es un fork de este repo.** Lo que cambia entre forks son los valores
del `config.yaml`, el mapa de registros Modbus, el modelo y la cuenta del proceso. La
plomería —captura, hilos, servidores, dataset, overlay— se cross-portea sin editarla.

Este archivo es la guía para arrancar un fork. El mapa del repo, los contratos entre
módulos y las decisiones vigentes están en [CLAUDE.md](CLAUDE.md); las convenciones de
nombres, comentarios y límites, en `.claude/skills/`.

## Cómo se corre

El intérprete del proyecto es el del venv, **no** el `python` del PATH:

    .venv\Scripts\python.exe -m pytest -q          # Windows
    .venv/bin/python -m pytest -q                  # Linux

Y la aplicación, con o sin ventana:

    .venv\Scripts\python.exe main.py                    # con interfaz
    .venv/bin/python main.py --headless                 # sin ventana
    .venv/bin/python main.py otro-config.yaml           # otro archivo de config

El modo headless también se fija con `ui.enabled: false` en el config —el flag manda sobre
la clave— y es el de un equipo en gabinete, un servicio del sistema o un contenedor sin
X11. Cablea exactamente lo mismo: Qt hace falta igual porque las señales son el vínculo
entre los hilos.

Python 3.10 de 64 bits. La suite completa corre sin hardware, sin GUI y sin red. Lo que
sí necesita hardware vive en `manual_test/<tema>/`, cada uno con su `config.yaml` al lado.

## Cómo se crea el repo del proyecto

Una aclaración de vocabulario antes: en este repo y en `CLAUDE.md`, **«fork» significa
proyecto derivado** —la instalación de una planta—, y no el fork de GitHub. Son cosas
distintas, y justamente el fork de GitHub es el que **no** conviene usar.

**No con un fork de GitHub**, por dos razones: GitHub no deja forkear un repo a la misma
cuenta que ya lo tiene, y —la que importa— **el fork de un repo público es público y no se
puede volver privado**, así que el `config.yaml`, el mapa de registros y los parámetros de
la planta del cliente quedarían expuestos.

Dos formas que sí sirven, y la diferencia entre las dos es la historia:

| | «Use this template» | Copia espejo |
|---|---|---|
| Cómo | Settings → General → ✅ *Template repository*, después el botón «Use this template» | los comandos de abajo |
| Historia | **un solo commit inicial** | **completa** |
| `git merge template/main` | falla: *refusing to merge unrelated histories* | funciona |
| Traer maquinaria nueva | sólo cherry-pick, a mano | merge normal |

Esa fila de la historia decide: sin ancestro común, cada mejora del template se cross-portea
a mano para siempre. **Copia espejo**, entonces:

```bash
# 1. En GitHub: crear el repo del proyecto VACÍO (sin README, sin .gitignore, sin licencia)

# 2. Copia espejo del template al repo nuevo
git clone --bare https://github.com/alejomancinelli/cv_projects_template.git
cd cv_projects_template.git
git push --mirror https://github.com/<cuenta>/<mi-proyecto>.git
cd .. && rm -rf cv_projects_template.git

# 3. Clonar el proyecto y enganchar el template como remoto
git clone https://github.com/<cuenta>/<mi-proyecto>.git
cd <mi-proyecto>
git remote add template https://github.com/alejomancinelli/cv_projects_template.git
git fetch template
```

Queda `origin` = el proyecto y `template` = de dónde vino. Los dos sentidos —traer
maquinaria nueva y devolver una mejora— están en «Cómo devolver una mejora al template».

### Lo que no viene en el clon

El `.gitignore` versiona tres carpetas pero no su contenido, así que un clon limpio no
trae:

- **`packages/`** — sólo el `.gitkeep`. El wheel de stapipy **no está en el repo**: hay que
  copiarlo ahí antes de correr `setup/cameras/{windows,linux}/sentech.*`. Ojo con la
  arquitectura: el `cp310-win_amd64` no sirve en la Jetson, que necesita el de aarch64.
- **`.venv/`** — se crea y se instala con `requirements.txt`.
- **`data/`** — logs y dataset se generan en runtime.

## Cómo se arranca un fork

1. **Crear el repo** como arriba, `pip install -r requirements.txt` en un venv nuevo y
   copiar el wheel de stapipy a `packages/`. Los SDK de cámara se instalan con los scripts
   de `setup/cameras/{windows,linux}/`. Cambiar la identidad del proyecto:
   `project.project_id` y `system.app_name` en el config, y `system/version.py`.
2. **`config.yaml`**: `project`, `system`, y una entrada en `cameras` por cámara física
   —marca, modelo, driver, IP, adquisición, ROI, mínimo de iluminación—. Verificar con
   `manual_test/cameras/camera_live_view.py`.
3. **El modelo**: implementar `AbstractModel` en un archivo nuevo de `system/inference/` y
   registrarlo con una línea en `model_factory.py`. Una clase por tarea —clasificación,
   detección, segmentación—, porque lo que cambia entre ellas es cómo se decodifica la
   salida. Los pesos afinados no son una clase nueva: son `path` en el config.
4. **`inference.models`** en el config: un slot por modelo, con su `type`, `path`,
   umbral y nombres de clase. El dispositivo no se configura: la implementación usa la
   GPU si hay CUDA y cae a CPU si no.
5. **`pipeline.py`**: declarar `model_slots` y escribir `_run()` con el orden de las
   etapas y sus cortocircuitos. Una sola etapa es el caso normal y ya viene escrito.
6. **`process:`** en el config: los parámetros de lo que se mide en esta planta —escala de
   píxel, umbrales, límites—, y `inference.pipelines` con qué cámaras entran a cada
   pipeline y cuántas imágenes son una medición (`frames_per_cycle`).
7. **`metrics.py`**: la cuenta del proceso, de las detecciones a los números que el
   cliente mide. Es lo que termina en el panel del anotado, en el JSON del dataset, en la
   telemetría y en los registros.
8. **`register_map.yaml`**: las filas `producer: inference` en el rango 3-50, y después
   `python -m system.modbus.export_map` para regenerar la tabla del integrador.
9. **`main.py`**: ya cablea todo. Se completan sus tres métodos marcados —
   `_build_monitor_content()`, `_build_analyzer()` y `_build_annotator()`—, que son los
   que leen `process:` y le pasan al motor lo del proyecto. El preprocessor del lente sale
   solo de la calibración del config.
10. **La vista de operador**, si la grilla de cámaras no alcanza: el widget propio se
    devuelve desde `_build_monitor_content()` de `main.py`. Los textos nuevos van a
    `ui/strings.py`, con su clave en inglés y una columna por idioma; el resto de la UI
    —ocho pestañas de configuración, cuatro de diagnóstico, temas y widgets— viene
    andando. Ver [docs/ui.md](docs/ui.md).
11. **`CLAUDE.md`**: actualizar el mapa y las decisiones con lo que el fork agregó.

## Qué se toca y qué no

Tres categorías, y la regla que las separa: **si un archivo describe cómo se hace algo,
es maquinaria y se cross-portea; si describe qué se mide en esta planta, es del fork.**

### Se reescriben en cada fork

| Archivo | Qué se escribe |
|---|---|
| `config.yaml` | todos los valores, y la sección `process:` completa |
| `system/modbus/register_map.yaml` | las filas de inferencia; el resto del mapa es plantilla |
| `system/inference/pipeline.py` | el orden de las etapas y sus cortocircuitos |
| `system/inference/metrics.py` | la cuenta del proceso |
| `system/version.py` | la versión del fork; se sube en cada release |
| `main.py` | **sólo el bloque «Lo que cambia en cada fork»**: el widget del monitor, el analyzer y los annotators. El resto es cableado genérico |
| `CLAUDE.md` | el mapa y las decisiones del fork |
| `README.md` | esta guía, reemplazada por la del proyecto |

### Se agregan, sin editar lo que ya está

| Archivo | Cuándo |
|---|---|
| `system/inference/<mi_modelo>.py` | el modelo del proyecto, más **una línea** en `model_factory.py` |
| `system/inference/annotations.py` | referencias de la planta: un límite de carga, una zona |
| `tools/camera/<mi_driver>.py` | una cámara de otro fabricante, más **una línea** en `camera_factory.py` |
| `tools/camera/camera_catalog.py` | un modelo de cámara que falte en el catálogo |
| el widget central del monitor | qué mira el operador; entra por `set_content()`. Una disposición propia —tres cámaras por pantalla, por ejemplo— son varias `CameraGrid(cfg, camera_slots=...)` dentro de ese widget |
| `ui/views/config/process_tab.py` | los campos de `process:`; la pestaña ya está registrada y vacía |
| `ui/views/config/<otra>_tab.py` | otra sección propia, más **una línea** en `_TAB_CLASSES` |
| `test/` | un test por archivo nuevo, espejando el árbol |
| `manual_test/<tema>/` | verificaciones con hardware, con su `config.yaml` al lado |
| `docs/` | notas de puesta en marcha, protocolos nuevos |

### Maquinaria: no se edita

Si hay que editar alguno de estos para que el fork funcione, el límite está mal puesto y
lo que falta es un punto de extensión, no un parche.

| Carpeta | Archivos |
|---|---|
| infraestructura | `system/config_manager.py`, `logger.py`, `paths.py`, `system_monitor.py` |
| captura | `system/camera/capture_thread.py`, `tools/camera/abstract_driver.py`, `camera_factory.py`, `basler_driver.py`, `st_driver.py`, `mock_driver.py`, `null_driver.py` |
| imagen | `tools/image/enhance.py`, `undistort.py` |
| inferencia | `system/inference/abstract_model.py`, `abstract_pipeline.py`, `model_factory.py`, `mock_model.py`, `null_model.py`, `result.py`, `overlay.py`, `analysis.py`, `engine.py` |
| bitfields | `system/formats/camera_health.py`, `com_status.py`, `system_status.py` |
| Modbus | `system/modbus/schema.py`, `registers.py`, `server.py`, `export_map.py` |
| telemetría | `system/telemetry/persistence.py`, `backends/*` |
| video | `system/video/abstract_video_server.py`, `http_server.py`, `rtsp_server.py` |
| dataset | `system/image_collector/collector.py`, `conditions.py` |
| interfaz | `ui/` entero salvo lo de arriba: `strings.py`, `theme.py`, `service_status.py`, `main_window.py`, las tres vistas, `views/config/*`, `views/diagnostics/*`, `widgets/*`, `dialogs/*`, `styles/*` |

Dos excepciones acotadas: `model_factory.py` y `camera_factory.py` reciben **una línea**
cada uno cuando el fork agrega una implementación. Eso es registro, no lógica.

### Generados: nunca a mano

`docs/modbus_map.md` y `docs/modbus_map.csv` salen de
`python -m system.modbus.export_map`. La fuente única es `register_map.yaml`.

## Cómo devolver una mejora al template

La tabla de arriba dice qué no se edita, no que la maquinaria sea intocable. La pregunta
no es *si* se puede mejorar sino **dónde va el cambio**, y hay tres respuestas distintas:

| Qué estás haciendo | Dónde va |
|---|---|
| La maquinaria no puede hacer algo que tu fork necesita | **Un punto de extensión en el template**, no un parche en el fork |
| Una mejora de la maquinaria que sirve a todos los forks | **El template**, por pull request |
| Cambiar qué se mide en esta planta | **Tu fork**, y no vuelve nunca |

La primera fila es la que se confunde. La regla completa es: *si hay que editar maquinaria
para que el fork funcione, el límite está mal puesto y lo que falta es un punto de
extensión.* Dos ejemplos reales de este repo:

- `CameraGrid` mostraba siempre todas las cámaras de `cameras:`. Un proyecto que quería
  tres por pantalla no necesitaba editar la grilla: necesitaba que la grilla aceptara
  **qué** cámaras mostrar. Salió un parámetro opcional `camera_slots`, compatible hacia
  atrás, y lo aprovechan todos los forks.
- El panel de configuración no se podía recargar cuando el `config.yaml` cambiaba por
  afuera. Salió `ConfigView.reload()`, público y documentado.

Ninguno de los dos se parcheó en un fork. Los dos entraron al template.

### Traer maquinaria nueva al proyecto

El sentido que se usa más seguido. Con la copia espejo hay ancestro común, así que es un
merge de verdad y no un cherry-pick:

```bash
git fetch template
git merge template/main
```

Qué esperar:

- **Lo que nunca tocaste entra limpio**, y es la mayoría: toda la tabla de maquinaria.
- **Los archivos que el fork reescribió conflictúan**, y no es una falla: `config.yaml`,
  `register_map.yaml`, `pipeline.py`, `metrics.py`, `version.py`, `CLAUDE.md` y
  `README.md` son las mismas rutas con contenido distinto a propósito.
- **Las dos fábricas** conflictúan en la línea de registro. Se resuelve dejando las dos.

**No se resuelven en automático con «la mía».** Git mergea por bloques, así que un cambio
del template en una parte del `config.yaml` que no tocaste entra solo —y eso normalmente
es lo que uno quiere: una clave nueva con su comentario—. Lo que hay que leer es si la
mejora de maquinaria vino con una clave de config nueva; si se descarta el bloque, el
código nuevo arranca sin ella:

```bash
git diff template/main -- config.yaml    # qué claves nuevas trae el template
```

Y conviene mergear seguido: diez merges chicos cuestan menos que uno de un año.

### La disciplina, y arranca el primer día

**Los commits de maquinaria van separados de los del proyecto.** Es lo único que hace
posible el pull request más adelante: si una mejora de `engine.py` queda en el mismo
commit que tu `metrics.py`, extraerla después es cirugía manual. Decidir «esto es
maquinaria o es mío» al momento de comitear no cuesta nada; reconstruirlo a los seis meses
sí.

### El flujo

```bash
git fetch template

# La rama sale del template, NO de tu trabajo: así el PR no arrastra tu config.yaml,
# tu metrics.py ni tu register_map.yaml.
git checkout -b mejora-engine template/main
# ...sólo archivos de la tabla «maquinaria»...

# Con permiso de escritura en el template —el caso normal si el template es tuyo—:
# la rama se empuja al template y el PR se abre ahí, rama -> main.
git push template mejora-engine

# Cuando se mergea, vuelve al proyecto:
git fetch template && git merge template/main
```

**Sin permiso de escritura en el template** hay que pasar por un fork de GitHub: los pull
requests entre repos distintos sólo existen dentro de una misma red de forks, así que un
repo creado por copia espejo no puede abrir uno. Se forkea el template en GitHub, se
empuja la rama a ese fork y el PR sale de ahí. El fork es sólo el vehículo del PR: el
proyecto sigue en su propio repo.

### Qué tiene que traer el PR

- **Un test.** `test/` espeja el árbol y la suite corre **sin hardware, sin GUI y sin
  red**: todo lo externo se reemplaza por dobles dentro del propio archivo de test.
- **Las convenciones.** `.claude/skills/` —`nomenclatura`, `comentarios`, `modularidad`—.
  Un PR que las ignora se va en revisar nombres y comentarios en vez del cambio.
- **El contrato actualizado.** Si cambia lo que un módulo promete, su docstring cambia en
  la misma edición.
- **La versión, si corresponde.** `system/version.py`: MINOR si agrega una clave de config
  con default, MAJOR si rompe hacia afuera —el mapa de registros, una clave obligatoria,
  el formato del JSON del dataset—.

### Lo que ya sabemos que va a chocar

`model_factory.py` y `camera_factory.py` reciben una línea de registro por fork. Dos forks
que agregan al mismo diccionario conflictúan ahí, y se resuelve en una línea. Es el precio
de que agregar un modelo o un driver no toque nada más.

### Qué cuesta no hacerlo

Un archivo de maquinaria divergente en el fork cuesta dos cosas. La visible es el merge:
duele en proporción a cuánto se parezca tu edición a lo que después cambie el template. La
peor es la otra: **se rompe el modelo mental compartido.** Quien depure tu fork —incluido
vos en un año— va a suponer que `engine.py` se comporta como el del template. Un parche
local silencioso es justo donde esa suposición muerde.

## La interfaz

`ui/` viene armada y andando: tres vistas —monitor, configuración, diagnóstico—, ocho
pestañas que cubren todas las secciones genéricas del `config.yaml`, cuatro de
diagnóstico, dos temas y los widgets reutilizables. Los tres huecos son a propósito: el
área central del monitor la llena el fork, la pestaña de proceso llega vacía porque sus
campos cambian en cada instalación, y el diálogo de GPIO espera un
`system/gpio_control.py` que todavía no existe.

Cómo se toca cada atributo —dónde vive un color, un texto, una medida; cómo se agrega
una pestaña o un widget; qué cosas la UI **no** hace— está en [docs/ui.md](docs/ui.md).

## Los cinco puntos de extensión del motor de inferencia

Un fork no edita `engine.py`: le pasa funciones por el constructor. Es lo que mantiene
genérica la maquinaria y testeable lo del proyecto.

| Punto | Qué hace | Dónde vive |
|---|---|---|
| `preprocessor` | sobre qué imagen se mide | `tools/image/undistort.py`, o del fork |
| `pipeline` | qué modelos corren y en qué orden | `pipeline.py` |
| `analyzer` | qué significan las detecciones | `metrics.py` |
| `annotator` | qué se dibuja además del resultado | `annotations.py` |
| `annotate_gate` | si alguien está mirando el stream anotado | `main.py`, del servidor de video |

## Los tres caminos que salen de un frame

No son el mismo frame, y confundirlos es el error caro:

| Camino | Qué imagen | Quién la prepara |
|---|---|---|
| medición | la del `preprocessor`: corregida si el lente lo pide | `tools/image/undistort.py` |
| dataset | la cruda de la cámara, para poder reentrenar | nadie la toca |
| visualización | ajustada para que se vea (gamma, contraste local) | `tools/image/enhance.py`, con `video.display` |

La corrección geométrica cambia **dónde está** un píxel, así que tiene que ser la misma
para el modelo y para el overlay: por eso va en el `preprocessor`, que define el frame de
referencia. El brillo cambia **cómo se ve** y no mueve nada: por eso va sólo en el camino
de visualización y no contamina el dataset.

## Cuando un ciclo son varias imágenes

Un proyecto que responde con N imágenes por medición declara N en
`inference.pipelines.<slot>.frames_per_cycle`. El motor no lo lee —emite un resultado por
frame y no promedia—: lo lee quien orquesta el ciclo, que junta esos N resultados y usa

- `analysis.summarize_metrics(results)` → media, desvío, mínimo y máximo por clave, más
  `sample_count`, todo plano y listo para la telemetría y los registros;
- `analysis.pick_representative(results)` → cuál de los N frames se guarda y se muestra,
  el más cercano al promedio medido en desvíos.
