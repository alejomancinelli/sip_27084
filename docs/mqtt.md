# Estructura de la telemetría en MQTT

Qué tópicos publica el equipo y qué trae cada mensaje. Es el contrato con quien se
suscriba: una vez que un flujo de Node-RED o un SCADA lee un tópico, moverlo lo rompe.

**Estado: implementado.** El backend es `system/telemetry/backends/mqtt_backend.py` y su
sección de config es `telemetry.mqtt`.

El público es quien consume el dato desde afuera en vivo —un Node-RED, un SCADA, un broker
que reparte a varios— y no quiere ni una base de series ni el PLC en el medio.

## Los datos son los mismos que los de InfluxDB

**No hay una lista de campos propia de MQTT.** Los registros los arma un solo lugar
—`PersistenceThread`, en `system/telemetry/persistence.py`— y los dos backends reciben
exactamente el mismo batch: si InfluxDB recibe `fps_estimated`, MQTT lo publica con ese
nombre y ese valor.

Así que **las series, los tags y los nombres de campo están en
[`influxdb.md`](influxdb.md) y no se repiten acá**. Duplicar esas tablas sería tener el
contrato en dos archivos, que es justo el bug que se evita teniéndolo en uno.

Lo que sí es propio de MQTT, y es lo que documenta este archivo, es **la forma en el
cable**: en qué tópico sale cada serie y cómo se ve el JSON.

## Los tópicos

Uno por serie, bajo el prefijo del equipo:

    <topic_base>/<measurement>

`topic_base` sale de `telemetry.mqtt.topic_base` del config —por convención el
`project_id`— y `measurement` es el nombre de la serie. Con `topic_base: 27084`:

| Tópico | Qué sale por muestra |
|---|---|
| `27084/sistema` | 1 mensaje: hardware del equipo |
| `27084/camara` | 1 por cámara |
| `27084/optica` | 1 por cámara: salud del vidrio |
| `27084/servicios` | 1 mensaje: los seis canales de salida |
| `27084/inferencia` | 1 por par (cámara, pipeline) con resultados nuevos, **más** 1 con las medias de la hora |

Los nombres de las series son los de este equipo, que **no son los del template**: ver la
advertencia de [`influxdb.md`](influxdb.md).

**La cámara y el pipeline no van en el tópico, van adentro del mensaje.** El tópico nombra
la serie, no la instancia: agregar una cámara no agrega tópicos, agrega mensajes en el que
ya existe. Un suscriptor que quiera una sola cámara filtra por el campo `camara_id`; uno
que las quiera todas se suscribe una vez y no se toca al sumar la tercera.

## El mensaje

Un objeto JSON plano con **los tags y los fields fusionados**, más `time`:

    {**tags, **fields, "time": <epoch_s>}

Plano porque MQTT no distingue tag de field: esa separación es de InfluxDB, y un suscriptor
que recibe dos objetos anidados tiene que saber cuál mirar. Fusionados, el mensaje se lee
de una.

`time` es **el instante en que se midió**, epoch en segundos, no el de la publicación.
Entre los dos puede pasar una ventana entera (ver el ritmo, más abajo), así que un
suscriptor que selle con la hora de llegada deforma la serie.

### Ojo: los fields pisan a los tags

La fusión es en ese orden, así que **una métrica que se llame igual que un tag lo
sobreescribe sin avisar**. Los tags son `proyecto`, `camara_id` y `pipeline`: una métrica
del analyzer llamada `camara_id` reemplazaría al slot en el mensaje y el suscriptor perdería
de qué cámara vino. Es la misma razón por la que en el mapa Modbus el nombre de la métrica es
el contrato: los nombres se eligen una vez y valen para todos los destinos.

## Ejemplos

Los valores son de ejemplo; los nombres y los tipos son los reales.

`27084/sistema`

```json
{
  "proyecto": "27084",
  "cpu_usage": 34, "gpu_usage": 61,
  "temp_cpu": 52, "temp_gpu": 58,
  "ram_mb": 3820, "ram_total_mb": 16384,
  "disk_gb": 214, "power_w": 18,
  "rx_eth0": 13, "tx_eth0": 1, "rx_eth1": 0, "tx_eth1": 0,
  "time": 1757340012.412
}
```

`27084/camara` — uno por cámara

```json
{
  "proyecto": "27084", "camara_id": "camera_1",
  "connected": 1, "capture_enabled": 1, "misconfigured": 0,
  "fps": 14.9, "temperatura": 41.2,
  "time": 1757340012.418
}
```

`27084/optica` — uno por cámara. Los cuatro últimos campos sólo salen si hubo alguna
medición: una cámara caída deja de publicarlos en vez de repetir el último valor bueno.

```json
{
  "proyecto": "27084", "camara_id": "camera_1",
  "estado": 0, "nitidez_max_pct": 96, "muestras": 2880, "referencia": 50.7,
  "varianza_max": 48.7, "varianza": 47.9, "nitidez_pct": 94, "luma": 11.8,
  "time": 1757340012.419
}
```

`27084/servicios` — el 1 significa lo mismo que el bit que lee el PLC: levantó y está
andando.

```json
{
  "proyecto": "27084",
  "modbus_tcp": 1, "modbus_rtu": 0,
  "video_http": 1, "video_rtsp": 0,
  "influxdb": 1, "mqtt": 1,
  "time": 1757340012.420
}
```

`27084/inferencia` — la medición, uno por par (cámara, pipeline).

```json
{
  "proyecto": "27084", "camara_id": "camera_1", "pipeline": "pipeline_1",
  "frames": 14, "invalid_count": 1, "invalid_reason": "dark_frame",
  "pct_pellet": 41, "pct_desmenuzado": 9, "pct_fondo": 50,
  "pct_carga": 63, "pct_pellet_norm": 82, "pct_desmenuzado_norm": 18,
  "confianza": 87, "iluminacion": 58, "inference_time_ms": 196.3,
  "time": 1757340012.431
}
```

`27084/inferencia` — las medias de la hora, **en todos los ticks**, aunque no se haya
medido nada. Sin tag de cámara: las ventanas son una sola para el equipo.

```json
{
  "proyecto": "27084",
  "pct_pellet_norm_1h": 80.4, "pct_desmenuzado_norm_1h": 19.6,
  "pct_carga_1h": 58.9,
  "cobertura_pct": 97, "material_presente_pct": 84,
  "inference_age_s": 1,
  "time": 1757340012.433
}
```

## El ritmo: ráfagas, no un mensaje por segundo

Los dos intervalos son los mismos que los de InfluxDB y están explicados allá: se **mide**
cada 1 s (`main.py`) y se **escribe** cada 10 s (`persistence.py`). Para un suscriptor eso
significa que los mensajes **no llegan de a uno por segundo**: llegan diez de golpe cada
diez segundos, cada uno con su propio `time`.

Un suscriptor que grafique tiene que usar el `time` de cada mensaje. Uno que sólo quiera el
valor actual se queda con el último de la ráfaga —que igual puede tener hasta 10 s—.

## Entrega: QoS 0, sin retain y sin sesión persistente

| | Qué implica para el suscriptor |
|---|---|
| **QoS 0** | lo que no se pudo publicar no se recupera |
| **sin retain** | al suscribirse no llega el último valor: hay que esperar la próxima ventana, hasta 10 s |
| **sin sesión persistente** | desconectarse y volver no recupera lo de mientras tanto |

Es lo que corresponde para telemetría periódica: el próximo valor llega enseguida y es más
útil que el viejo. Un dato que **no** puede perderse no va por acá: va al PLC por Modbus,
que es sincrónico y lo lee el que decide.

Mientras el backend no tenga sesión con el broker, `write()` descarta. Un corte deja un
hueco, y el hueco también informa.

## Identidad y credenciales

- El **client_id** es `system.device_id` del config. Dos equipos con el mismo `device_id`
  contra el mismo broker **se echan mutuamente**: es lo primero que hay que mirar si una
  serie aparece a saltos.
- El **usuario** va en `telemetry.mqtt.user` porque identifica; la **password** sale de la
  variable de entorno `MQTT_PASSWORD` y nunca del config, que se versiona y se edita desde
  la UI.
- Sin `paho-mqtt` instalado el backend degrada a no-op y lo deja en su `status`. No hay que
  preguntarle si está disponible.

## El canal es de una sola dirección

El backend **publica y no se suscribe a nada**: no hay comando, ni configuración, ni
disparo que entre por MQTT. Lo que entra al equipo desde afuera es el bloque escribible del
mapa Modbus, que el template no usa. Un proyecto que necesite recibir por MQTT está
agregando un camino de entrada, no ampliando este backend.

## Agregar un tópico

No se agrega acá. Un tópico nuevo es una serie nueva, y una serie nueva es un `push_data()`
más en `main.py` con un `measurement` que todavía no existe: sale por MQTT y por InfluxDB a
la vez, sin tocar ningún backend. Si además tiene que llegar al PLC, la métrica necesita su
fila en `system/modbus/register_map.yaml` con el mismo nombre.
