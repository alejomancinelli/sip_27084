# Cómo se construye el repositorio de firma de licencias

**Este documento es el encargo completo de un repositorio nuevo.** Está escrito para que
otra instancia de Claude lo lea sin tener a la vista el repo del producto y pueda
construirlo entero: el formato está especificado byte por byte más abajo, no por
referencia.

El repositorio a construir se llama **`licensing`** y es **privado**. No se forkea, no se
entrega, no se compila dentro de ningún producto.

---

## 1. Qué problema resuelve, en una línea

Los equipos de visión artificial que entrega IEA corren un binario compilado que sólo
funciona en la máquina para la que se emitió su licencia. **Este repositorio es la
máquina de emitir esas licencias**: guarda la clave privada, firma los archivos y lleva
el registro de qué se emitió a quién.

El otro lado —el que verifica— ya existe y está en el repo del producto, en
`system/license/`. **Este repositorio no puede cambiar el formato**: lo consume un binario
que ya está instalado en una planta.

---

## 2. Reglas duras, antes de escribir una línea

1. **La clave privada nunca se commitea.** Ni cifrada, ni en un test, ni en un ejemplo, ni
   en un `.env.example`. El `.gitignore` la cubre desde el primer commit, antes de que
   exista.
2. **Nada de este repositorio se copia al repo del producto.** El producto sólo recibe la
   clave *pública*, que es un string de 43 caracteres que se pega a mano.
3. **Cero dependencias de red.** Es una herramienta de línea de comandos que corre en la
   máquina de quien firma. No hay servidor, no hay API, no hay base de datos. Si algún día
   hay un portal, se construye encima de esto.
4. **Toda emisión queda registrada.** Una licencia firmada que no está en el registro es
   una licencia que no se va a poder reemitir cuando el cliente llame.
5. **El formato es un contrato.** Cualquier cambio que rompa el token deja afuera a las
   plantas que ya están instaladas. El campo `schema` existe justamente para eso.

---

## 3. El formato del archivo de licencia — especificación normativa

### 3.1 El token

Un archivo `.lic` es **una sola línea de texto ASCII**:

```
<payload_b64url>.<signature_b64url>
```

- `payload_b64url`: el payload JSON codificado en **base64 URL-safe sin relleno** — el
  alfabeto es `A-Z a-z 0-9 - _`, y los `=` finales se sacan.
- `signature_b64url`: los **64 bytes** de la firma Ed25519, con la misma codificación.
- El separador es un punto. No hay tercera parte.

### 3.2 Qué se firma exactamente

**Se firma sobre los bytes del payload, los mismos que se codifican en base64.** No sobre
el JSON reserializado por el verificador, no sobre el token completo, no sobre un hash
previo.

```
payload_bytes = canonical_json(payload_dict)
signature     = Ed25519.sign(private_key, payload_bytes)
token         = b64url(payload_bytes) + "." + b64url(signature)
```

El verificador hace `b64url_decode` de la primera parte y verifica la firma **contra esos
bytes tal como vinieron**. Eso significa que el firmante es libre de serializar como
quiera y el token seguirá validando; el JSON canónico de abajo es una convención para que
dos emisiones consecutivas se puedan comparar con un `diff`, no un requisito de
corrección. Aun así, **implementarlo canónico**: cuando haya que auditar una licencia
vieja, se va a agradecer.

### 3.3 JSON canónico

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
```

Claves ordenadas alfabéticamente, sin espacios, escapes ASCII para todo lo no-ASCII.

### 3.4 Los campos del payload

```json
{
  "schema": 1,
  "license_id": "IEA-2026-0042",
  "key_id": "iea-1",
  "product": "cv_projects_template",
  "client": "ACME",
  "project_id": "LINEA_3",
  "issued_at": "2026-09-07T00:00:00Z",
  "expires_at": null,
  "policy": "warn",
  "fingerprint": {
    "components": {
      "board_uuid": "c7180e56…",
      "board_serial": "9faf3503…",
      "disk_serial": "60e70e45…"
    },
    "min_matches": 2
  },
  "entitlements": {
    "max_cameras": 4,
    "features": ["core", "security_detection"],
    "model_hashes": {}
  }
}
```

| Campo | Tipo | Obligatorio | Qué es y qué valida el otro lado |
|---|---|---|---|
| `schema` | int | sí | Debe ser exactamente `1`. Otro número se rechaza con «hace falta una versión más nueva del software» |
| `license_id` | string no vacío | sí | Identificador de la emisión. Es lo que el cliente lee por teléfono |
| `key_id` | string no vacío | sí | Con qué clave pública se verifica. Ver §4 |
| `product` | string | no | Informativo, para el registro. El verificador no lo mira |
| `client` | string | no | Debe coincidir con `project.client` del `config.yaml` del equipo, si ambos están |
| `project_id` | string | no | Debe coincidir con `project.project_id`. Ver la advertencia de §3.6 |
| `issued_at` | fecha ISO | sí | Informativo |
| `expires_at` | fecha ISO o `null` | sí (puede ser null) | `null` = perpetua. Ver §3.5 |
| `policy` | `"off"`, `"warn"` o `"degrade"` | no | Qué hace el equipo si la licencia no vale. Un valor desconocido cae en el default del binario |
| `fingerprint.components` | objeto `{fuente: sha256_hex}` | sí | La huella. Ver §3.7 |
| `fingerprint.min_matches` | int >= 0 | sí | El N del N-de-M. **No puede ser mayor que la cantidad de componentes**: el verificador rechaza el archivo por malformado |
| `entitlements.max_cameras` | int >= 1 o `null` | no | Cupo de cámaras. `null` o ausente = sin cupo. **Un booleano se rechaza** |
| `entitlements.features` | lista de strings | no | Addons habilitados. `"core"` es el pipeline normal |
| `entitlements.model_hashes` | objeto `{model_slot: sha256_hex}` | no | SHA-256 de los pesos que la licencia habilita. `{}` = no se verifica ninguno |

### 3.5 Fechas

- Formato ISO 8601. Se acepta el sufijo `Z` y también un offset explícito.
- **Una fecha sin hora vence al final de ese día en UTC.** `"expires_at": "2027-03-31"`
  significa válida durante todo el 31 de marzo. La licencia se vende por día.
- Una fecha sin zona se interpreta como UTC.
- Emitir siempre con `Z` explícito para no depender de esa regla.

### 3.6 `client` y `project_id` no son seguridad

Los dos valores contra los que se comparan salen del `config.yaml` del equipo, que el
cliente puede editar. **No atan nada**: el que ata es el fingerprint. Están para que
mandar el archivo equivocado se note el primer día. No razonar sobre ellos como si fueran
un control de acceso.

### 3.7 Cómo se calcula la huella — algoritmo normativo

El equipo lee valores de hardware y **manda los hashes, nunca los valores**. El firmante
recibe los hashes ya calculados en el archivo de solicitud y los copia tal cual al
payload. **Este repositorio no calcula huellas.** Se documenta el algoritmo igual, porque
hay que poder auditarlo:

```python
def hash_value(source: str, value: str) -> str:
    text = value.replace("\x00", "").strip().upper()
    text = re.sub(r"\s+", " ", text)
    # Descartado: menos de 4 caracteres, relleno de fábrica, o un solo carácter repetido
    return hashlib.sha256(f"{source}={text}".encode("utf-8")).hexdigest()
```

El nombre de la fuente va **adentro** del hash: sin eso, un serial de disco igual a un
serial de placa contaría como dos coincidencias siendo un solo dato.

Fuentes posibles: `board_uuid`, `board_serial`, `module_serial` (el serial del módulo de
la Jetson), `disk_serial`, y una `mac_<interfaz>` por placa de red física.

**Al emitir, no copiar todas las fuentes sin pensar.** Las MAC y el disco cambian con un
reemplazo de pieza; `board_uuid`, `board_serial` y `module_serial` no. Una licencia
robusta lleva las cinco fuentes con `min_matches: 2`: sobrevive a un disco o una placa de
red cambiados, y no sobrevive a una máquina nueva.

### 3.8 El caso sin lock de máquina

`"components": {}` con `"min_matches": 0` es una licencia que valida en **cualquier**
equipo. Es un caso legítimo —una demo, una feria— y el verificador lo acepta a propósito.
La herramienta tiene que **exigir un flag explícito** (`--no-machine-lock`) y escribirlo
en el registro con todas las letras: emitirla por accidente es el peor error posible de
esta herramienta.

---

## 4. Las claves

- **Algoritmo: Ed25519.** No es negociable, es lo que el verificador implementa.
- La clave pública son **32 bytes**; se publica en base64url sin relleno (43 caracteres).
- La firma son **64 bytes**.

### Cuántas claves — una

**Una sola, para todos los proyectos, todos los países y todos los clientes.** No se emite
una por fork ni una por año.

La clave contesta una sola pregunta: *¿firmó esto IEA?* **No** contesta *¿para qué
instalación es?* — eso son `project_id`, `client`, `features` y el fingerprint, que van
firmados adentro del payload y sí se verifican.

El argumento que decide es la custodia: todas las privadas vivirían en este mismo
repositorio y en la misma caja fuerte, así que diez claves no aíslan diez radios de daño
—quien entra a la máquina de firma se las lleva todas—, sólo multiplican por diez lo que
hay que respaldar y perder. Y cada clave nueva obliga a recompilar y actualizar todos los
equipos que tengan que aceptarla.

El único caso que justifica una segunda clave es **otro firmante**: un socio o una filial
que emite licencias por su cuenta y a quien no se le quiere dar la capacidad de firmar
para los clientes propios. Ahí sí hay una frontera de confianza real.

### `key_id`

Cada licencia dice con qué clave se firmó. El binario del producto lleva una tabla
`key_id -> clave pública`, y ese id existe para que **rotar sea posible**, no para correr
varias claves en paralelo.

**Convención de nombres: un contador. `iea-1`, y la rotación siguiente es `iea-2`.**

Sin año y sin letra, a propósito. El verificador usa ese string para una sola cosa: elegir
con qué clave probar la firma. Nunca lo valida como alcance, así que un `iea-2026-ar`
daría por válida una licencia de cualquier otro año y de cualquier otro país — un nombre
que miente. Y un año en el nombre se lee como vencimiento: las claves no vencen, las
licencias sí. La fecha de creación va al registro, que es donde se puede consultar.

Lo único que el id tiene que cumplir: **no repetirse nunca**, porque queda escrito en cada
licencia emitida bajo él.

### Rotar es de dos etapas

Esto es lo que hay que tener claro antes de necesitarlo. **Agregar la clave nueva no
revoca nada.** Mientras la comprometida siga en la tabla del binario, el que la robó firma
licencias que el equipo acepta.

| Etapa | Qué se publica | Qué logra |
|---|---|---|
| 1 | Un build con las **dos** claves; se reemite a todas las plantas con el `key_id` nuevo | Nadie se queda afuera: las licencias viejas todavía validan |
| 2 | Un build con la clave vieja **borrada** de la tabla | Recién acá la clave robada deja de servir |

Entre las dos etapas el esquema está comprometido y no hay mucho que hacer al respecto,
así que la etapa 2 se planifica junto con la 1 y no «cuando haya tiempo». La etapa 2 sólo
puede correr cuando **ninguna licencia viva** use la clave vieja, y saber eso es
exactamente para lo que está `registry.csv`.

El comando `list --key-id iea-1` tiene que contestar «quién sigue con la clave vieja»,
porque es la pregunta que bloquea la etapa 2.

### Custodia

La clave privada se guarda cifrada con passphrase, y la passphrase **no está en el
repositorio ni en el mismo disco**. Un `keys/` en el `.gitignore` desde el primer commit.
Documentar en el README quién tiene la passphrase y dónde está el respaldo: una clave
privada perdida significa que ninguna licencia futura puede emitirse para las plantas ya
instaladas hasta publicar una versión nueva del binario.

---

## 5. El archivo de solicitud, que es la entrada

Lo genera el equipo del cliente con `python -m system.license request` y llega por mail.
Es JSON legible:

```json
{
  "schema": 1,
  "generated_at": "2026-09-07T14:57:38+00:00",
  "app_version": "0.3.0",
  "client": "CLIENT_NAME",
  "project_id": "PROJECT_ID",
  "device_id": "DEVICE_ID",
  "hostname": "DESKTOP-H4I34GI",
  "platform": "Windows 10 (AMD64)",
  "fingerprint": {
    "components": { "board_uuid": "c7180e56…", "board_serial": "9faf3503…" },
    "suggested_min_matches": 2
  },
  "declared": { "cameras": 1, "features": ["core"] }
}
```

`declared` dice qué tiene el equipo según su propio config. **Sirve para cruzar contra lo
que se vendió**: si la solicitud declara ocho cámaras y la orden de compra dice cuatro, eso
se ve acá y no en la planta. La herramienta tiene que **avisar** cuando el cupo que se está
por emitir es menor que lo declarado, y seguir igual si el que firma lo confirma: puede ser
exactamente lo que se quiere.

---

## 6. Qué construir

### 6.1 Estructura

```
licensing/
  README.md              qué es, cómo se emite una licencia, dónde está la passphrase
  CLAUDE.md              el mapa del repo y las decisiones, como en el repo del producto
  requirements.txt       cryptography>=42.0 y nada más (pytest para los tests)
  .gitignore             keys/ y *.pem desde el primer commit
  licensing/
    keys.py              generar, cargar y guardar el par de claves
    schema.py            el formato del payload: construir y validar
    signer.py            firmar un payload y armar el token
    request.py           leer y validar un archivo de solicitud
    registry.py          el registro de lo emitido
    cli.py               los subcomandos
    __main__.py          punto de entrada
  test/
    test_schema.py
    test_signer.py
    test_registry.py
    test_round_trip.py   ver §8: es el test que importa
  keys/                  vacío y en el .gitignore
  issued/                los .lic emitidos, uno por archivo, versionados
  registry.csv           el registro
```

### 6.2 Convenciones de código

Las mismas que el repo del producto, porque las van a leer las mismas personas:

- **Comentarios y docstrings en español**; **nombres de identificadores en inglés**.
- `snake_case` en todo salvo clases, que son `PascalCase`. Constantes en `UPPER_SNAKE`.
- Prefijo `_` para lo interno del módulo o de la clase; **nunca** en variables locales ni
  en parámetros.
- Todos los parámetros anotados con tipo. Nada de defaults mutables.
- Docstrings sin bloques `Args:` / `Returns:`; los tipos van en la firma.
- Valores físicos con la unidad en el sufijo del nombre.
- Un módulo se tiene que poder leer y testear sin abrir otro.

### 6.3 Los comandos

```
python -m licensing keygen --key-id iea-1
    Genera un par Ed25519, guarda la privada cifrada en keys/ e IMPRIME la clave
    pública lista para pegar en system/license/public_key.py del producto.
    Se niega a pisar una clave existente sin --force.

python -m licensing sign solicitud.json \
    --license-id IEA-2026-0042 \
    --key-id iea-1 \
    --max-cameras 4 \
    --features core,security_detection \
    --expires 2027-03-31          (omitido = perpetua)
    --policy warn \
    --min-matches 2 \
    --model-hash model_1=<sha256> (repetible)
    --out issued/IEA-2026-0042.lic
    Valida la solicitud, arma el payload, firma, escribe el .lic y AGREGA LA FILA
    AL REGISTRO. Sin registro no hay emisión: es una sola operación.

python -m licensing inspect archivo.lic
    Muestra el payload en claro y dice si la firma cierra con alguna clave conocida.
    Es lo que se corre cuando un cliente llama diciendo que no le anda.

python -m licensing list [--client ACME] [--expiring-in 60] [--key-id iea-1]
    Lee el registro. `--expiring-in` es el comando que se corre una vez por mes para
    no enterarse de un vencimiento por el reclamo del cliente. `--key-id` contesta
    «quién sigue con la clave vieja», que es la pregunta que bloquea la etapa 2 de
    una rotación (ver §4).

python -m licensing reissue IEA-2026-0042 --request solicitud_nueva.json
    Reemisión: toma los entitlements de una licencia ya emitida y los vuelve a firmar
    contra una huella nueva. Es el caso del hardware reemplazado en garantía, y tiene
    comando propio porque tiene que ser rápido y no depender de que alguien se acuerde
    de qué se le había vendido. Marca la anterior como reemplazada en el registro.
```

### 6.4 El registro

`registry.csv`, una fila por emisión, versionado en git — el historial de git **es** la
auditoría:

```
license_id,issued_at,client,project_id,key_id,expires_at,max_cameras,features,
min_matches,fingerprint_sources,machine_lock,hostname,platform,app_version,
replaces,notes
```

- `fingerprint_sources`: los nombres de las fuentes, no los hashes. Alcanza para diagnosticar.
- `machine_lock`: `yes` / `no`, para que una licencia sin lock se vea de un vistazo.
- `replaces`: el `license_id` que esta reemisión deja atrás.

Un CSV y no una base de datos a propósito: se lee con cualquier cosa, se versiona, y
sobrevive a que la herramienta no se toque durante dos años.

---

## 7. Errores que esta herramienta tiene que hacer imposibles

Cada uno de estos merece una validación explícita y un test:

1. **Emitir sin lock de máquina por accidente** — exige `--no-machine-lock`.
2. **Emitir con `min_matches` mayor que la cantidad de componentes** — la licencia no
   validaría en ninguna máquina, y el cliente se entera en la planta.
3. **Emitir con `min_matches: 0` y componentes presentes** — se lee como si estuviera
   atada y no lo está.
4. **Emitir un `license_id` repetido** — rompe el registro y la trazabilidad.
5. **Emitir contra una solicitud de otro cliente** — si el `client` de la solicitud no
   coincide con el `--client` de la orden, preguntar antes de seguir.
6. **Emitir con una huella de menos de dos componentes** — sobrevive a nada; avisar.
7. **Firmar sin escribir el registro** — las dos cosas son una sola operación.
8. **Un `expires_at` en el pasado** — se firma una licencia nacida vencida; avisar.

---

## 8. El test que importa: round-trip contra el verificador real

Los tests unitarios de cada módulo son necesarios y obvios. El que decide si este
repositorio sirve es otro:

> Firmar una licencia con este repositorio y verificarla con el código del producto,
> sin compartir código entre los dos lados.

Cómo hacerlo sin acoplar los repos: el test trae **una copia congelada** del verificador
—`system/license/schema.py` y `system/license/verify.py`, unas 300 líneas sin dependencias
más allá de `cryptography`— bajo `test/vendor/`, con un `README` que diga de qué commit
del producto salió. El test firma, verifica con esa copia y compara campo por campo.

Si el producto cambia el formato, ese test es el que avisa: se actualiza la copia y se ve
qué se rompió. **Sin ese test, este repositorio puede emitir durante meses licencias que
ninguna planta acepta**, y eso se descubre el día de una puesta en marcha.

Casos del round-trip, como mínimo:

- Perpetua, válida, con cupo y features → el verificador la da por válida.
- Vencida → el verificador dice vencida, no inválida.
- Huella de otra máquina → el verificador dice que es de otro equipo.
- Payload editado a mano después de firmar → la firma no cierra.
- `key_id` desconocido → el verificador lo dice como clave desconocida.
- Emitida con cinco componentes y `min_matches: 2`, verificada con dos componentes
  cambiados → sigue válida. **Este es el caso del hardware reemplazado y es el que
  protege al cliente que pagó.**

---

## 9. El procedimiento operativo, que va en el README

La parte no técnica, y la que decide si esto ayuda o lastima. Escribirla explícitamente:

1. **Quién puede firmar.** Nombres, no roles.
2. **Qué se pide antes de firmar.** Número de orden de compra o equivalente.
3. **Cuánto tarda una reemisión.** Un número, comprometido. El caso es: una Jetson falla
   en garantía, se reemplaza, la huella cambia, y **el que se queda sin producción es el
   cliente que pagó**. Sin un tiempo de respuesta comprometido, este esquema lastima al
   que compró y no al que copia.
4. **Qué se hace si se pierde o se filtra la clave privada.** Son dos casos distintos y
   conviene no mezclarlos:
   - **Perdida** (sin respaldo, nadie más la tiene): no se puede emitir ni reemitir nada
     para las plantas instaladas. Se genera una clave nueva y se publica un build que la
     agregue; los equipos que ya andan siguen andando con su licencia actual, pero
     cualquier renovación exige actualizarles el software primero.
   - **Filtrada** (alguien más la tiene): la rotación de dos etapas de §4, y la etapa 2
     —borrar la clave vieja de la tabla— es la que cierra el agujero. Hasta que corra,
     el que la robó puede firmar licencias que los equipos aceptan.
   Que esté escrito es lo que hace que alguien se ocupe del respaldo y de la custodia.
5. **Revisión mensual de vencimientos** con `list --expiring-in 60`.

---

## 10. Fuera de alcance, y por qué

- **Un servidor o portal web.** Las plantas no tienen internet; el archivo viaja por mail.
  Si algún día hay volumen para justificarlo, se construye encima de esta herramienta y el
  formato no cambia.
- **Revocación.** No hay canal para avisarle a un equipo offline que su licencia se
  revocó. Lo más cercano es no renovar una licencia anual. No implementar un `revoke` que
  no revoca nada: sería un cartel que da falsa tranquilidad.
- **Cifrar los pesos del modelo.** Es otro problema. Un `.engine` de TensorRT ya está
  atado a la arquitectura de GPU y a la versión de TensorRT por construcción.
- **Telemetría de activaciones.** Requiere red.

---

## 11. Criterio de terminado

El repositorio está listo cuando:

- `python -m licensing keygen --key-id iea-1` imprime una clave pública que, pegada
  en `system/license/public_key.py` del producto, hace validar una licencia firmada acá.
- Los ocho errores de §7 tienen validación y test.
- El round-trip de §8 pasa, incluido el caso de la pieza reemplazada.
- `registry.csv` tiene una fila por cada `.lic` en `issued/`, y el test lo verifica.
- El README contesta las cinco preguntas de §9 con nombres y números concretos, no con
  generalidades.
