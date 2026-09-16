# SIP Smart Belt Monitor — Cargill APG 27084

Mide la composición del material que pasa por una cinta: qué proporción es **pellet
entero**, qué proporción viene **desmenuzado**, y cuán **cargada** está la cinta. Una cámara
GigE sobre la cinta, un YOLO de segmentación en una NVIDIA Jetson, y el resultado al PLC por
Modbus, a Grafana por InfluxDB, a video por HTTP/RTSP y a un dataset en disco.

Qué se publica y con qué nombres:

| Destino | Dónde está el contrato |
|---|---|
| PLC (Modbus TCP/RTU) | [`docs/modbus_map.md`](docs/modbus_map.md), generado desde el YAML |
| Dashboard (InfluxDB) | [`docs/influxdb.md`](docs/influxdb.md) |
| Broker (MQTT) | [`docs/mqtt.md`](docs/mqtt.md) |

Es un fork del template de la familia: la plomería —captura, hilos, servidores, dataset,
overlay, licencia, interfaz— se cross-portea sin editarla. Qué es de este proyecto y qué
viene del template está en [CLAUDE.md](CLAUDE.md), junto con los contratos entre módulos y
las decisiones vigentes; las convenciones de nombres, comentarios y límites, en
`.claude/skills/`.

> **Antes de poner esto en producción hay dos cosas que se deciden mirando la cinta**: subir
> la exposición de la cámara hasta que el frame crudo tenga el nivel con el que el modelo
> viene trabajando —la versión anterior lo amplificaba por software y eso ya no llega a la
> inferencia— y recalibrar `process.dark_background_threshold` contra esa imagen. Ver
> «Qué todavía no existe» en [CLAUDE.md](CLAUDE.md).

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

## De dónde viene este repo

Es un fork del template de la familia, con el template enganchado como remoto para poder
traer maquinaria nueva y devolver mejoras:

```bash
git remote -v
# origin     <el repo de este proyecto>
# template   <el repo del template>
```

Cómo se trae maquinaria nueva del template y cómo se le devuelve una mejora está más abajo,
en «Cómo devolver una mejora al template».

## Cómo se pone en marcha un equipo

1. **Instalar**: `pip install -r requirements.txt` en un venv nuevo y copiar el wheel de
   stapipy a `packages/`. El SDK de la cámara se instala con los scripts de
   `setup/cameras/{windows,linux}/`. En la Jetson, además, torch y torchvision salen del
   índice de NVIDIA y no de PyPI. Copiar `.env.example` como `.env` y completar los
   secretos del equipo —`INFLUXDB_TOKEN`, sobre todo—: el `.env` no se versiona.
2. **El modelo**: copiar el `.engine` a `models/` y apuntar
   `inference.models.segmenter.path`. **El `.engine` se compila en la Jetson**: está atado a
   la arquitectura de GPU y a la versión de TensorRT, así que no se puede generar en la PC de
   desarrollo y hay que rehacerlo si cambia JetPack.
3. **La cámara**: verificar con `manual_test/cameras/camera_live_view.py` que la Sentech
   entrega frames, y ajustar `cameras.camera_1.acquisition`.
4. **La exposición** (ver el aviso de arriba): subirla hasta que el frame **crudo** tenga el
   nivel con el que el modelo viene trabajando, y recalibrar
   `process.dark_background_threshold` contra esa imagen.
5. **El rectángulo de cinta y el umbral de fondo oscuro**: pestaña **Proceso** del panel de
   configuración. El rectángulo se dibuja sobre el video en vivo con «Dibujar sobre el
   video...», que es como se hace en planta; el umbral es por clase y en 0 no refina nada.
   Los dos van a `process:` del config.
6. **La óptica**: calibrar con el vidrio recién limpio desde la pestaña de configuración,
   ya con la exposición definitiva. Sin calibrar, el estado es «no disponible» y nunca
   alarma: un vidrio limpio que nadie midió no se afirma.
7. **El PLC**: `modbus.tcp` y/o `modbus.rtu`, y pasarle al integrador
   [`docs/modbus_map.md`](docs/modbus_map.md). **Ojo con el registro 51**: pasó del enum 0-5
   de la versión anterior al bitfield del template, así que la lógica del PLC sobre ese
   registro hay que actualizarla.
8. **El dashboard**: `telemetry.influxdb` con la org y el bucket, y el token por `.env`. Los
   nombres de las series son los de la versión anterior y el dashboard existente funciona
   sin editar un panel — ver [`docs/influxdb.md`](docs/influxdb.md).
9. **La versión**: subir `APP_VERSION` en `system/version.py` en el commit que cierra el
   cambio. Es el número que el operador lee por teléfono cuando algo anda mal.

## Qué se toca y qué no

Tres categorías, y la regla que las separa: **si un archivo describe cómo se hace algo,
es maquinaria y se cross-portea; si describe qué se mide en esta cinta, es de este
proyecto.**

### De este proyecto

| Archivo | Qué tiene |
|---|---|
| `config.yaml` | todos los valores, y la sección `process:` completa |
| `system/modbus/register_map.yaml` | el mapa que ya lee el PLC; las direcciones no se mueven |
| `system/inference/models/yolo_seg_model.py` | el segmentador sobre ultralytics |
| `system/inference/pipeline.py` | una etapa: el segmentador de la cinta |
| `system/inference/metrics.py` | composición y carga, por conteo de píxeles por unión |
| `system/inference/annotations.py` | el rectángulo de cinta y el panel de composición |
| `system/version.py` | la versión del equipo; se sube en cada release |
| `main.py` | el bloque «Lo que cambia en cada fork», **más** la divergencia de telemetría y el cableado de GPIO y medias móviles |
| `CLAUDE.md`, `README.md` | el mapa, las decisiones y esta guía |

### Agregados que valen para el template

Genéricos, no tienen nada de esta planta y conviene devolverlos —ver «Cómo devolver una
mejora al template» más abajo—:

| Archivo | Qué es |
|---|---|
| `system/gpio_control.py` | entradas y salidas digitales por libgpiod; el template lo declaraba pendiente |
| `system/formats/gpio_status.py` | las dos palabras de GPIO que van al PLC |
| `system/inference/rolling.py` | media móvil por ventana de tiempo, con su cobertura |
| `ui/dialogs/roi_dialog.py` | **una línea**: el prefijo de config pasó a ser parámetro, para poder dibujar más de un rectángulo por cámara |

### Se agregan, sin editar lo que ya está

| Archivo | Cuándo |
|---|---|
| `tools/camera/<mi_driver>.py` | una cámara de otro fabricante, más **una línea** en `camera_factory.py` |
| `tools/camera/camera_catalog.py` | un modelo de cámara que falte en el catálogo |
| el widget central del monitor | hoy es la grilla genérica; un widget propio entra por `_build_monitor_content()` de `main.py`, sin tocar nada más |
| `ui/views/config/process_tab.py` | ya tiene los campos de `process:` de este equipo; se le agregan los que el proceso sume |
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
| captura | `system/camera/capture_thread.py`, `tools/camera/abstract_driver.py`, `camera_factory.py`, `basler_driver.py`, `st_driver.py`, `rtsp_driver.py`, `mock_driver.py`, `null_driver.py` |
| imagen | `tools/image/enhance.py`, `undistort.py` |
| inferencia | `system/inference/models/*` (menos `yolo_seg_model.py`), `abstract_pipeline.py`, `result.py`, `overlay.py`, `analysis.py`, `engine.py` |
| bitfields | `system/formats/camera_health.py`, `com_status.py`, `system_status.py` (`gpio_status.py` es de acá) |
| Modbus | `system/modbus/schema.py`, `registers.py`, `server.py`, `export_map.py` |
| telemetría | `system/telemetry/persistence.py`, `backends/*` |
| video | `system/video/abstract_video_server.py`, `http_server.py`, `rtsp_server.py` |
| dataset | `system/image_collector/collector.py`, `conditions.py` |
| licencia | `system/license/*` entero. La clave pública no se edita en ningún fork: la genera el repositorio de firma al compilar, en un `_public_key.py` que no se versiona |
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

El sentido que se usa más seguido. Con el injerto o con la copia espejo hay ancestro común,
así que es un merge de verdad y no un cherry-pick:

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
requests entre repos distintos sólo existen dentro de una misma red de forks, y el repo del
proyecto no está en la del template por ninguno de los dos caminos —ni el botón ni la copia
espejo crean un fork de GitHub—. Se forkea el template en GitHub, se
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
`system/gpio_control.py`, que este proyecto sí trae: el botón aparece cuando `main.py`
le inyecta el controlador.

Cómo se toca cada atributo —dónde vive un color, un texto, una medida; cómo se agrega
una pestaña o un widget; qué cosas la UI **no** hace— está en [docs/ui.md](docs/ui.md).

## Los seis puntos de extensión del motor de inferencia

Un fork no edita `engine.py`: le pasa funciones por el constructor. Es lo que mantiene
genérica la maquinaria y testeable lo del proyecto.

| Punto | Qué hace | Dónde vive |
|---|---|---|
| `preprocessor` | sobre qué imagen se mide | `tools/image/undistort.py`, o del fork |
| `pipeline` | qué modelos corren y en qué orden | `pipeline.py` |
| `classifier` | qué detecciones cuentan y con qué clase | **sin usar acá**: no hay nada calibrado por cámara |
| `analyzer` | qué significan las detecciones | `metrics.py`, con `process:` |
| `annotator` | qué se dibuja además del resultado | `annotations.py`, con `process:` |
| `annotate_gate` | si alguien está mirando el stream anotado | `main.py`, del servidor de video |

El `classifier` corre entre el pipeline y el promedio de confianza, y es el único lugar
donde entra lo que depende de la cámara: `predict()` no recibe el slot, porque el modelo es
uno por pipeline y lo comparten todas sus cámaras. Ahí van la escala de píxel, el filtro de
tamaño y la clase que sale de la medida. Lo que descarta no cuenta para `min_detections` ni
para la confianza del resultado, y la clase que deja en `class_index` es la que después
colorean el overlay y `analysis.count_by_class`. El analyzer no puede hacerlo: su contrato
dice que no modifica el resultado.

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

**Este equipo mide una imagen por medición** (`frames_per_cycle: 1`), así que el scheduler
es un pass-through y cada frame sale como su propia medición, igual que en la versión
anterior. Lo que sigue es para el día que eso cambie.

Un proyecto que responde con N imágenes por medición declara N en
`inference.pipelines.<slot>.frames_per_cycle`. El motor no lo lee —emite un resultado por
frame y no promedia—: lo lee quien orquesta el ciclo, que junta esos N resultados y usa

- `analysis.summarize_metrics(results)` → media, desvío, mínimo y máximo por clave, más
  `sample_count`, todo plano y listo para la telemetría y los registros;
- `analysis.pick_representative(results)` → cuál de los N frames se guarda y se muestra,
  el más cercano al promedio medido en desvíos.
