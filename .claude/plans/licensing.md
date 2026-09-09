# Plan: licencia atada al hardware

**Estado: diseñado, sin empezar.** Es el tema B2 del `roadmap.md`, tomado y con diseño
propio. Lo que el roadmap dejaba abierto —qué se licencia, qué pasa cuando la licencia no
vale, quién firma, cómo se renueva— está decidido acá; lo que sigue abierto está marcado
como tal en «Lo que falta decidir».

**La dependencia sigue en pie: sin compilar (B3), esto es un cartel y no una cerradura.**
Sacar la validación de un `.py` es borrar un `if`. Todo lo de abajo está diseñado para que
la parte que se puede parchear sea chica y esté en código máquina, pero el orden de trabajo
no se invierte: se puede escribir y testear todo esto antes de compilar, no *entregar*.

---

## Qué protege, y qué no

| Objetivo | Lo cumple |
|---|---|
| Que el equipo entregado no corra en otra máquina | Huella N-de-M + firma Ed25519 |
| Que no se agreguen cámaras sin pagarlas | `max_cameras` |
| Que un addon —detección de seguridad, por ejemplo— corra sólo donde se pagó | `features` por pipeline |
| Que una licencia de una planta no sirva en otra | `client` + `project_id` en el payload |
| Que los pesos copiados no arranquen | `model_hashes` |
| Que una licencia anual venza | `expires_at`, con `null` = perpetua |
| Que copiar sea **deliberado y no accidental** | todo lo anterior junto |

Lo que **no** protege: al que tiene el hardware, root y ganas. Eso no lo protege ningún
esquema del lado del cliente, y el roadmap ya lo dice. El piso real es el contrato.

---

## El archivo de licencia

Formato `{payload_b64url}.{signature_b64url}` en **`license.lic` en la raíz del repo**,
al lado del `config.yaml`. La ruta sale de `system/paths.py` y no del directorio desde el
que se lanzó el proceso, por el motivo que ese módulo ya explica: un servicio arranca en
`C:\Windows\system32` y el config puede ser otro archivo.

El payload es JSON canónico (claves ordenadas, sin espacios) para que firmar y verificar
serialicen igual:

    {
      "license_id":  "IEA-2026-0042",
      "key_id":      "iea-1",
      "product":     "cv_projects_template",
      "client":      "CLIENT_NAME",
      "project_id":  "PROJECT_ID",
      "issued_at":   "2026-09-04T00:00:00Z",
      "expires_at":  null,
      "policy":      "degrade",
      "fingerprint": {
        "components":  {"board_uuid": "<sha256>", "disk_serial": "<sha256>", "mac_eth0": "<sha256>"},
        "min_matches": 2
      },
      "entitlements": {
        "max_cameras":  4,
        "features":     ["core", "security_detection"],
        "model_hashes": {"model_1": "<sha256 de los pesos>"}
      }
    }

Tres decisiones que no se ven en el JSON:

- **`key_id` va adentro.** Sin él, rotar la clave privada obliga a reflashear cada planta.
  El binario lleva un mapa `key_id -> clave pública` y verifica con la que la licencia
  nombra; una licencia que nombra una clave que el binario no tiene es inválida, no un
  crash. **Hay una sola clave para todos los proyectos** —lo que ata la licencia a una
  instalación es la huella, no el nombre de la clave— y el id es un contador, `iea-1`:
  sin año ni país, porque el verificador nunca lo valida como alcance y un nombre que
  sugiere un alcance que no se comprueba miente. Rotar es de dos etapas y la segunda
  —sacar la clave vieja de la tabla— es la que sirve de algo; el procedimiento está en
  `licensing-signer-repo.md`.
- **`policy` viaja firmada.** Es lo que decide qué pasa cuando la licencia no vale, y va
  en la licencia y no en el `config.yaml` —que el cliente edita— ni compilada —que
  obligaría a un build por decisión comercial—. Sin archivo de licencia manda el default
  compilado. Con esto, la charla pendiente con el equipo no bloquea el desarrollo: cambia
  un campo, no el código.
- **Cada componente de la huella se hashea por separado.** Un hash único de todo junto no
  permite el N-de-M: cualquier cambio da otro número y no hay forma de saber *qué* cambió.

---

## Módulo nuevo: `system/license/`

Carpeta propia porque son varios archivos, según la regla del `CLAUDE.md`. Nivel 0–1: sin
Qt, sin Modbus, sin ConfigManager salvo donde se aclare, y sin más I/O que archivos.

    system/license/
      schema.py        nivel 1 — dueño del formato: campos, JSON canónico, parse/serialize
      verify.py        nivel 0 — firma Ed25519 contra la clave pública embebida
      public_key.py    las claves públicas por `key_id`; en operación normal, una sola
      fingerprint.py   nivel 0 — huella por componente; nunca levanta excepción
      clock.py         nivel 0 — último-visto firmado + monotónico; detecta retroceso
      manager.py       la fachada: carga, verifica, evalúa entitlements, `get_status()`
      request.py       arma el archivo de solicitud que el cliente manda por mail
      __main__.py      CLI: `request` | `status` | `install <archivo>`

**Sólo `manager.py` lo importa `main.py`.** Los demás son internos del subsistema, y esa
es la razón de la carpeta: la fachada expone un objeto de estado y un dict de status, con
la misma forma que ya tienen los demás subsistemas.

### `fingerprint.py` — qué se lee y por qué N-de-M

Devuelve `dict[str, str]`: nombre de la fuente → SHA-256 de su valor. Una fuente que no se
puede leer **no está en el dict**; el módulo no levanta excepción nunca, porque una huella
incompleta es un dato y no un error.

| Fuente | Windows | Linux / Jetson | Estabilidad |
|---|---|---|---|
| `board_uuid` | `Win32_ComputerSystemProduct.UUID` (WMI) | `/sys/class/dmi/id/product_uuid` | alta; no existe en Jetson |
| `module_serial` | — | `/proc/device-tree/serial-number` | alta; es la fuente buena de la Jetson |
| `disk_serial` | serial del disco de sistema | `/sys/block/<dev>/device/serial` | media: cambia si se reemplaza el disco |
| `mac_<iface>` | MAC de las NIC físicas | idem | media: una placa de red agregada suma, no cambia |
| `cpu_id` | modelo + cantidad de cores | `/proc/cpuinfo` | baja como identidad; sirve de desempate |

`min_matches: 2` sobre tres o cuatro componentes es el default propuesto: sobrevive a un
disco o una NIC cambiados y no sobrevive a una máquina nueva. **`/etc/machine-id` no se
usa**: cambia al reinstalar el sistema, que es justo lo que pasa después de una falla.

La tabla de fuentes es inyectable, para que los tests corran sin depender de la máquina que
los corre.

### `clock.py` — el retroceso de reloj

Dos mecanismos, porque ninguno solo alcanza:

- **Último visto en disco**, en `data/license_state.bin`: el instante más alto que se vio,
  con un HMAC derivado de la huella de la máquina para que editarlo a mano no sirva. Si el
  reloj actual es menor que el guardado, hay retroceso: la licencia queda sospechosa y
  aplica la política, con el motivo explícito en el log. Sobrevive al reboot.
- **`time.monotonic()`** para «cuánto lleva corriendo este proceso», que es lo que el
  re-chequeo periódico necesita. No se puede retroceder, y se reinicia al reboot: por eso
  va con el de arriba y no en lugar de él.

Que el archivo de estado falte no es un error —es la primera corrida—; que esté y no
valide, sí.

### `manager.py` — la fachada

    LicenseManager(config)
      .state          -> valid | invalid | absent | expired | foreign | unlicensed_build
      .policy         -> "off" | "warn" | "degrade"
      .max_cameras    -> int | None      (None = sin límite)
      .has_feature(name) -> bool
      .days_remaining -> int | None      (None = perpetua)
      .recheck()      -> bool            (True si el estado cambió; lo llama el timer)
      .get_status()   -> dict            para la UI y el log, mismo patrón que el resto

`get_status()` devuelve claves estables: `state`, `policy`, `license_id`, `client`,
`project_id`, `expires_at`, `days_remaining`, `fingerprint_matches`, `features`,
`max_cameras`, `reason`. La UI no valida nada: muestra esto.

### Dónde se decide que el build es el que enforcea

Nuitka define `__compiled__` en cada módulo compilado. `manager.py` mira eso:

    IS_COMPILED = "__compiled__" in globals()

Corriendo desde fuentes el estado es `unlicensed_build` y la política es `off`, con **una
línea de log en INFO** para que nunca sea silencioso. Así la suite completa y los
`manual_test/` siguen andando sin fixtures de licencia y sin tocar un solo test existente.

**No hay variable de entorno ni clave de config que apague esto.** Un `LICENSE_DEV=1` es
un string en el binario y es lo primero que se busca.

*A verificar en la primera prueba de build*: que `__compiled__` esté también con Cython
selectivo, si se va por ese lado en vez de Nuitka completo. Si no, la marca la pone el
build (un módulo generado con una constante) y el criterio no cambia.

---

## Dónde se enforcea, archivo por archivo

La regla es la de siempre: **la maquinaria no pregunta si hay licencia; `main.py` es el
único que sabe.** Es el mismo argumento por el que ningún `QThread` importa a otro.

### 1. Arranque — `main.py`

Inmediatamente después de `load_env_file()` y del logging, antes de instanciar nada:

    self._license = LicenseManager(config)

Así el veredicto queda en el log aunque después falle cualquier otra cosa, que es
exactamente cuando hace falta leerlo.

### 2. `max_cameras` — el loop de cámaras de `Application.__init__`

Los slots que exceden el cupo **no reciben `CaptureThread`**. `main.py` les publica un
status con `misconfigured` y un `reason` de licencia, que es el camino que ya existe para
una cámara mal configurada: la grilla la muestra en su lugar con el motivo, en vez de
dejar un hueco.

El corte es por orden de declaración en `cameras:` —`camera_1` primero— y **es
determinista**: que la cámara que se apaga cambie entre arranques sería peor que el
límite mismo.

`tools/camera/camera_factory.py` no se toca. Es `tools/`: no conoce la app y menos su
licencia.

### 3. `features` por pipeline — clave nueva de config + el loop de pipelines

    inference:
      pipelines:
        pipeline_1:
          enabled: true
          feature: core             # unidad comercial; ausente = "core"

En el loop que ya filtra por `enabled`, un `if` más: un pipeline cuyo `feature` no está en
la licencia no se instancia, se loguea una vez y su ausencia se ve en la palabra de estado.
Es la misma forma que el filtro que ya está al lado.

**Un feature por pipeline, y dos propósitos son dos pipelines.** Medir un proceso y
vigilar una zona no se juntan en un pipeline aunque miren la misma cámara: son dos hilos
—`interval_s` es por pipeline—, sus resultados ya se separan por el par `(cámara,
pipeline)`, y sobre todo se venden por separado. Juntarlos haría que el cliente que no
compró la vigilancia pierda también la medición que sí pagó, porque el pipeline entero no
arranca. `manager.read_feature()` es el dueño de esa lectura: devuelve el nombre, y avisa
si el config trae una forma que no corresponde en vez de dejar el pipeline bloqueado en
silencio.

**Por qué el nombre y no el tipo de modelo**: la unidad comercial es el addon, no el
framework. Dos productos distintos pueden correr el mismo `type: yolo_trt`, y el mismo
addon puede cambiar de framework entre versiones sin que cambie lo que el cliente compró.

### 4. `model_hashes` — en `manager.py`, no en el modelo

El manager lee `inference.models.<slot>.path` del config, hashea los archivos que la
licencia nombra y compara. **`system/inference/` no se toca.**

Es a propósito: el hash de integridad de B1 —«¿es el modelo que corresponde?»— es otra
pregunta, va en `AbstractModel.load()` al lado de `_verify_task()`, y sirve aunque no haya
licencia. Mezclarlos deja al subsistema de inferencia importando al de licencia por una
verificación que puede hacer el que ya lee el archivo.

Costo a medir: hashear un `.engine` de cientos de MB en el arranque. Si molesta, se hashean
los primeros N MB más el tamaño, y eso se declara en el formato.

### 5. Vencimiento y re-chequeo — un timer más en `main.py`

Junto a los tres que ya están (`_REGISTERS_INTERVAL_MS`, `_STATUS_INTERVAL_MS`,
`_SAMPLE_INTERVAL_MS`), un cuarto con `_LICENSE_INTERVAL_MS`. Sin esto, congelar el reloj
antes de arrancar y no reiniciar nunca es todo lo que hace falta para correr para siempre.

El re-chequeo **no vuelve a leer el archivo del disco por defecto**: revalida vencimiento,
reloj y huella sobre el payload ya verificado. Volver a leer el archivo es lo que hace el
botón «instalar licencia» de la UI, y ahí sí re-verifica firma.

### 6. Qué hace la política

| | `off` (desde fuentes) | `warn` | `degrade` |
|---|---|---|---|
| Cámaras sobre el cupo | corren | **no corren** | **no corren** |
| Pipelines sin feature | corren | **no corren** | **no corren** |
| Métricas al PLC | van | van | **no se publican; los registros quedan congelados** |
| Telemetría | va | va | **no se publica** |
| Heartbeat, salud, palabras de estado | van | van | **van** |
| Streams de video | van | van | van |
| Bit de licencia | apagado | **prendido** | **prendido** |
| Log | una línea INFO al arrancar | WARNING al arrancar y en cada cambio | idem |

**Los entitlements se aplican también con `warn`.** «Warn» es sobre la licencia inválida
—vencida, de otra máquina, ausente—, no sobre el cupo: una licencia válida de cuatro
cámaras no habilita cinco por más suave que sea la política.

Y la fila que importa: **con `degrade`, Modbus sigue sirviendo.** Bajar el canal deja al
PLC viendo un enlace muerto, que es indistinguible de un cable cortado — el integrador
sale a buscar el problema equivocado. Lo que se corta es la *medición*, no el canal: el
heartbeat late, las palabras de estado se publican, el bit de licencia dice por qué los
números dejaron de moverse.

---

## Cómo se ve desde afuera

Los tres canales que el repo ya tiene, sin inventar uno nuevo.

### `system/formats/system_status.py`

Bit nuevo, con los 4–15 libres:

    BIT_LICENSE_INVALID = 4   # licencia ausente, vencida o de otra máquina
    WORD_MASK = 0x001F

Encaja con lo que la palabra responde —«¿se puede confiar en las mediciones?»— y con el
criterio de sus otros cuatro bits: **el sistema sigue publicando números verosímiles
mientras pasa**. Con `warn` los publica una instalación sin licencia; con `degrade` quedan
los últimos, viejos.

### `system/modbus/register_map.yaml`

El bloque de salud 81-99 tiene libre de 90 en adelante:

    - addr: 90
      name: license_days_remaining
      desc: Días hasta el vencimiento de la licencia; 0 = vencida o sin licencia
      producer: health
      unit: días

Una perpetua publica un centinela alto y constante. Sirve para lo que el bit no puede: que
el PLC alarme **treinta días antes** y no el día que la línea se para. En una licencia
anual esa fila es la diferencia entre un aviso y una parada.

### UI — pestaña en diagnóstico

`ui/views/diagnostics/license_tab.py`, +1 línea en `_TAB_CLASSES` de
`ui/views/diagnostics_view.py`. Diagnóstico y no configuración: la licencia es estado, no
un parámetro que el operador edita.

Qué muestra: estado con chip, `license_id`, cliente y proyecto, vencimiento y días
restantes, cuántos componentes de la huella dan match y cuáles, y los entitlements
—cámaras, features, modelos—.

Qué hace, que es lo que tiene que ser simple para el cliente:

- **«Exportar solicitud»** — guarda `license_request.json` en un pendrive o en el
  escritorio. Es la huella, el `project_id` y la versión; nada secreto, así que puede
  viajar por mail.
- **«Instalar licencia»** — abre un `.lic`, lo verifica **antes** de copiarlo, y si vale lo
  escribe en la raíz y refresca el estado sin reiniciar. Si no vale, dice por qué y no
  toca el archivo que ya estaba: una renovación fallida no puede dejar al equipo peor que
  antes.

Los textos van a `ui/strings.py` en es/en/pt, como todos. El chip usa
`ui/widgets/status_chip.py`. **Los seis chips de servicio de la barra de estado no se
tocan**: esa lista es `com_status` y significa «canales de salida», meter licencia ahí le
cambia el significado. Cuando la licencia no vale, el aviso permanente va en el header,
al lado de la versión y el `project_id` — que es donde el operador ya mira cuando llama
por teléfono.

El mismo flujo sin pantalla: `python -m system.license request|status|install`, para el
equipo en gabinete y para el headless.

---

## El lado que firma — repositorio aparte

**No va en este repo.** Este se forkea por instalación y se compila para el cliente;
cualquier cosa de firma acá viaja a todos lados. Repo privado propio con:

- Generación y custodia del par de claves (la privada nunca se commitea; idealmente en un
  token o en un `.gpg` con passphrase).
- `sign.py`: toma el `license_request.json` + los entitlements y escribe el `.lic`.
- Registro de lo emitido —quién, qué, cuándo, con qué huella— para poder reemitir sin
  adivinar. Un CSV alcanza al principio.
- Procedimiento de rotación de `key_id`.

Este repo lleva sólo la clave **pública**, el generador de solicitud, el verificador y la
documentación.

### Reemisión, que es el riesgo real

El roadmap ya lo marca y vale repetirlo: **el que se queda afuera es el cliente que
pagó**. El N-de-M cubre el disco y la placa de red; una Jetson entera reemplazada no.
Antes de entregar la primera licencia tiene que existir el procedimiento —quién firma,
con qué autorización, en cuánto tiempo— y tiene que estar escrito en `docs/licensing.md`,
que es lo que el cliente lee.

Una licencia perpetua con vencimiento `null` y un procedimiento de reemisión de dos días
es un producto. Sin el procedimiento es una bomba de tiempo con la mecha del lado del que
vende.

---

## Tests

`test/system/license/`, espejando el árbol como todo el resto. Sin hardware, sin red, sin
GUI: el par de claves de prueba se genera en el propio test —Ed25519 es instantáneo— y las
licencias se firman ahí mismo.

Qué cubrir:

- Firma válida, firma inválida, payload manoseado, `key_id` desconocido.
- Vencida, por vencer, perpetua.
- Huella: N-de-M en el borde (justo alcanza / justo no), una fuente ausente, todas ausentes.
- `project_id` y `client` que no corresponden.
- Archivo ausente, archivo ilegible, archivo con basura.
- Retroceso de reloj: estado guardado adelante del reloj actual.
- Entitlements: cupo de cámaras exacto y excedido, feature ausente, hash de modelo distinto.
- Que la política `off` desde fuentes no bloquee nada.

Del cableado se testea que `main.py` no instancie el pipeline sin feature ni la cámara
sobre el cupo — con un manager falso, no con una licencia real.

---

## Dependencia nueva

`cryptography` (Ed25519) en `requirements.txt`. Hay wheels cp310 para win_amd64 y para
aarch64 manylinux, así que no rompe el pin de Python 3.10 del wheel de stapipy.
Alternativa si aparece fricción en la Jetson: `PyNaCl`. Implementar Ed25519 a mano no es
una alternativa.

---

## Orden de trabajo

    1. schema + verify + fingerprint + clock + tests      puro, sin cablear, sin riesgo
    2. manager + cableado en main.py + bit + registro     acá empieza a hacer algo
    3. UI: pestaña de diagnóstico + exportar/instalar     el flujo que ve el cliente
    4. repo de firma + docs/licensing.md + procedimiento  sin esto no se entrega nada
    5. B3: compilar                                       sin esto, todo lo anterior es un cartel

Los pasos 1 a 3 se pueden hacer y testear sin decidir la política, porque la política
viaja en el payload. El paso 4 **es** el producto: un esquema técnico impecable sin
procedimiento de reemisión lastima al cliente y no al que copia.

---

## Lo que falta decidir

1. **`warn` o `degrade` como default de fábrica**, y si cambia entre clientes. Charla
   pendiente con el equipo; no bloquea nada del 1 al 3.
2. **Confirmar `__compiled__`** con la cadena de build que se elija (Nuitka completo o
   Cython selectivo). Es una tarde de prueba y decide cómo se marca el build.
3. **`min_matches` y qué fuentes** entran en la huella de cada plataforma. Depende del
   hardware real: hay que correr `fingerprint.py` en una Jetson y en un equipo Windows de
   planta y ver qué devuelve cada uno.
4. **Si el hash del modelo entra en la v1** o queda para cuando se sepa si el modelo va a
   ser un `.engine` (que ya es device-locked por construcción — roadmap C4/B1).
5. **Vencimiento del soporte vs. vencimiento del software.** Una perpetua con soporte anual
   es otro campo, no otro mecanismo: `support_until` al lado de `expires_at`, que no apaga
   nada y sólo se muestra. Vale definirlo antes de emitir la primera, porque agregar un
   campo después obliga a reemitir todas.
