# Plan: proteger los pesos del modelo

**Estado: implementado en el repo del producto.** Falta lo que no es código, que es lo
mismo que le falta a la licencia: la **clave real de cada fork** —la genera y la custodia
el repositorio de firma, con el encargo de `model-protection-signer-repo.md`— y
**compilar** (roadmap B3). Desde el código fuente los pesos van en claro y todo anda igual.

Este documento se escribió para que otra instancia pudiera tomarlo sin haber visto la
conversación que lo originó: el razonamiento está acá, no por referencia. Lo que quedó
construido está en `docs/model_protection.md`, que es el documento operativo; acá queda el
porqué de cada decisión.

Es el tema **B1** del roadmap, tomado y redefinido. El roadmap lo planteaba como «hash del
modelo»; la conclusión de este plan es que el hash **no** es lo que hace falta, y que
cifrado autenticado resuelve mejor las tres cosas que se querían.

---

## 1. Qué se protege, y por qué éste y no otro

De todo lo que se entrega, **los pesos son lo más caro de reproducir y lo único que un
agente no puede rehacer**. La plomería —captura, Modbus, telemetría, la interfaz— se
reescribe con ayuda; el dataset etiquetado y las semanas de entrenamiento, no.

Hoy los pesos viajan como un archivo suelto en la ruta que dice
`inference.models.<slot>.path`. Copiarlo a un pendrive y cargarlo en otro lado es cuestión
de segundos, y no hace falta ninguna habilidad.

## 2. Dos amenazas distintas, dos mecanismos que no se sustituyen

| Amenaza | Lo detiene | Estado |
|---|---|---|
| El cliente **extrae los pesos** y los usa en otro programa, o se los pasa a un tercero | **Cifrado de los pesos** | lo que agrega este plan |
| El cliente **copia la instalación entera** a un segundo equipo | **Huella de la licencia** | ya está (`system/license/`) |

**Ninguno reemplaza al otro.** El cifrado no frena la instalación copiada: el binario
copiado descifra igual en el equipo nuevo. La licencia no frena la extracción de los
pesos: el archivo está ahí a la vista. Hacen falta los dos y son independientes.

Hay una tercera vía que sale gratis y conviene tener presente: **un `.engine` de TensorRT
ya está atado al equipo por construcción** —depende de la arquitectura de GPU y de la
versión de TensorRT, y no carga en otra máquina—. Si el proyecto exporta a TensorRT, esa
protección viene sola. Pero **no se puede contar con ella**: el mismo template corre en
Windows sobre `.pt` o `.onnx`, y ahí no hay nada equivalente. Por eso este plan no depende
del formato.

## 3. La decisión de fondo: cifrado autenticado, no hash

**Se usa AEAD —AES-256-GCM o ChaCha20-Poly1305— y no «hash + cifrado» por separado.**

Un AEAD da las dos propiedades en una sola pasada:

- **Confidencialidad**: los bytes no sirven sin la clave.
- **Integridad**: el tag de autenticación falla si el archivo se modificó, aunque sea un
  bit.

Es decir que **el SHA-256 desaparece del diseño**: no hay hash que guardar, ni manifiesto
que mantener, ni nada que comparar. Si descifra y el tag cierra, el archivo es auténtico y
está íntegro. Si no, no carga.

### Por qué NO va el hash en la licencia

La licencia tiene un campo `entitlements.model_hashes` y funciona
(`LicenseManager._find_model_mismatch()`). **Para este flujo de trabajo no se usa**, y el
motivo es operativo: reentrenar es frecuente, sobre todo al principio de un proyecto, y
con el hash en la licencia **cada reentrenamiento obliga a reemitir la licencia**.

Con cifrado y una clave estable, reentrenar es: cifrar el modelo nuevo con la misma clave,
copiarlo al equipo. **Sin reemitir licencia y sin recompilar.** Ese es exactamente el
flujo de «cambiar el modelo y listo».

El campo se deja en el formato porque no cuesta nada y algún fork puede quererlo; en las
entregas normales va vacío.

### Qué NO logra, dicho de frente

La aplicación descifra para poder inferir, así que **en tiempo de ejecución los pesos en
claro existen en la memoria de un equipo que administra el cliente**. Quien esté dispuesto
a enganchar un debugger y volcar el buffer los obtiene. Igual que con la licencia, esto no
se arregla del lado del cliente.

Lo que sí compra: **la extracción casual deja de funcionar por completo**. Ya no hay un
`.pt` para arrastrar a un pendrive. Extraerlos pasa a requerir una persona con ganas y con
oficio de ingeniería inversa. Contra la amenaza realista —el cliente o su integrador que
se copia un archivo porque estaba a mano— eso es casi todo el valor.

---

## 4. LO PRIMERO: la pregunta que decide si este plan sirve

**¿El framework del proyecto puede cargar los pesos desde memoria, sin un archivo?**

Si insiste en una ruta, habría que escribir el plano a disco para cargarlo, y eso tira a
la basura casi todo el beneficio. **Hay que contestar esto antes de escribir una línea**,
contra el framework que el fork use de verdad.

| Framework | Cómo se carga desde memoria | Veredicto |
|---|---|---|
| **TensorRT** | `runtime.deserialize_cuda_engine(buffer)` | directo, toma bytes |
| **PyTorch** | `torch.load(io.BytesIO(data))` | directo |
| **ONNX Runtime** | `InferenceSession(model_bytes)` | directo |
| **Ultralytics `YOLO(path)`** | quiere una ruta; hay que construir desde el checkpoint a mano | con fricción |
| **OpenCV DNN** | según versión; algunas aceptan buffer | verificar |

El caso de la Jetson —TensorRT— es el más fácil de los cinco.

**Contestada: el fork carga desde memoria.** Los tres candidatos reales —TensorRT,
PyTorch y ONNX Runtime— toman bytes, y Ultralytics queda como el caso con fricción. Por
eso `_read_weights()` devuelve bytes y ninguna implementación recibe una ruta. La
respuesta **se confirma en la máquina de cada fork**, no leyendo esta tabla:
`manual_test/model_protection/check_protected_weights.py --weights <el modelo real>`
descifra en memoria y lo carga con el framework que haya instalado.

**Si el framework elegido no puede**, este plan cambia de forma y hay que decidir entre:
descifrar a un archivo temporal que se borra enseguida (protección mucho menor, honesta de
declarar), o usar un directorio en memoria (`/dev/shm` en Linux; en Windows no hay
equivalente limpio). No inventar una tercera vía sin discutirlo.

---

## 5. Dónde va, arquitectónicamente

**Detrás de `AbstractModel`, no en cada implementación.** El contrato del modelo ya vive
en `system/inference/models/abstract_model.py`, y ahí es donde `load()` confronta la tarea
declarada con `_verify_task()`: el descifrado va por el mismo camino y con la misma forma
«falla antes del primer frame».

La base gana algo como:

    def _read_weights(self, path: str) -> bytes:
        """Devuelve los bytes de los pesos, descifrando si vienen cifrados."""

y cada implementación concreta pasa a cargar desde bytes en vez de desde ruta. Así:

- **Ningún fork reimplementa el descifrado.** Agregar un modelo sigue siendo implementar
  la clase y sumar una línea a la fábrica.
- **El formato se detecta por el archivo, no por config.** Un encabezado mágico dice si
  está cifrado; si no lo tiene, se devuelve tal cual.

Esa última propiedad importa más de lo que parece: **desde fuentes, con pesos sin cifrar,
todo sigue andando sin clave ni configuración**. Es el mismo criterio que el resto del
subsistema de licencia —la protección aparece en el build entregado, no estorba el
desarrollo— y evita que el equipo de desarrollo tenga que manejar claves para probar.

### Formato del archivo

    magic     8 bytes   identifica el formato y su versión
    version   1 byte    para poder cambiarlo sin romper lo entregado
    nonce     12 bytes  único por archivo; NUNCA se reusa con la misma clave
    ciphertext
    tag       16 bytes  el que hace de verificación de integridad

Nombre: el del modelo con sufijo (`best.engine` → `best.engine.enc`), para que se vea de
un vistazo qué está protegido y qué no.

**El nonce no se reusa jamás con la misma clave.** Se genera al azar al cifrar. Reusarlo
con GCM rompe la confidencialidad de los dos archivos, y es el error clásico de estos
esquemas.

---

## 6. La clave

**Una clave por fork.** Cada instalación ya se compila para su cliente, así que una clave
por fork sale casi gratis — y la propiedad que da es buena: **una clave sacada del binario
de un cliente expone el modelo de ese cliente y de ningún otro.** Con una sola clave para
todas las entregas, un solo binario comprometido las expone todas.

**No se guarda como una constante de 32 bytes.** Un blob así se encuentra con un volcado de
strings incluso en código compilado. Se deriva en tiempo de ejecución de varias piezas
—HKDF sobre fragmentos separados más un salt del fork—, de modo que no haya un único valor
que buscar.

Ser honestos con lo que eso es: **subir el costo, no cerrar la puerta**. Alguien con
oficio la recupera. La derivación evita el caso fácil, que es el que va a pasar.

### Cómo entra la clave al binario

Un módulo generado en tiempo de build (`system/inference/models/_model_key.py`, en el
`.gitignore`) que Nuitka compila junto con el resto. **Nunca se commitea**, igual que la
clave privada de la firma.

### Custodia

La clave de cada fork vive en el repositorio de firma —el mismo que ya custodia la clave
privada y lleva el registro de licencias emitidas—, en la fila de ese cliente. Ahí ya hay
un lugar con la disciplina correcta; no hace falta inventar otro.

### Rotación

Si una clave se filtra, se expone el modelo de ese cliente. La respuesta es: clave nueva,
recifrar los pesos, rebuild y reentrega. No hay nada que revocar a distancia.

---

## 7. La herramienta de cifrado

Un script que toma los pesos en claro y la clave del fork, y escribe el `.enc`. **Va en el
repositorio de firma**, junto a la custodia de claves, no en este repo: este se forkea y
se entrega, y una herramienta de cifrado acá viaja al cliente. El encargo completo —los
comandos, el registro y el formato byte por byte— está en
`model-protection-signer-repo.md`, escrito para copiarlo a ese repo y ejecutarlo ahí.

Uso previsto en un reentrenamiento:

    1. Se entrena y se valida el modelo nuevo (fuera de esto).
    2. Se cifra con la clave del fork.
    3. Se copia el `.enc` al equipo, reemplazando el anterior.
    4. Se reinicia la aplicación.

Sin reemitir licencia. Sin recompilar. Ése es el punto.

---

## 8. Qué se toca en este repo

| Archivo | Qué |
|---|---|
| `system/inference/models/abstract_model.py` | `_read_weights()`, la detección del formato y el descifrado |
| `system/inference/models/<cada implementación>` | cargar desde bytes en vez de desde ruta |
| `system/inference/models/_model_key.py` | **generado en el build**, gitignoreado, nunca commiteado |
| `requirements.txt` | nada nuevo: `cryptography` ya entró con la licencia y trae AES-GCM |
| `.gitignore` | el módulo de clave generado |
| `test/inference/models/` | los tests de abajo |
| `docs/` | cómo se reemplaza un modelo en un equipo entregado |

**Ninguna clave de `config.yaml` nueva.** El archivo dice dónde están los pesos y nada
más; que estén cifrados o no lo dice el archivo mismo.

---

## 9. Tests

Sin hardware, sin GPU, sin framework de inferencia — con una clave de prueba generada en
el propio test, como se hace en `test/license/`:

- Round-trip: cifrar y descifrar devuelve los bytes originales.
- Clave equivocada: falla, y falla con un motivo legible.
- Archivo modificado de a un bit: el tag no cierra y **no carga**. Es la prueba de que la
  integridad sale gratis.
- Archivo truncado: falla sin excepción rara.
- Archivo **sin cifrar**: se devuelve tal cual. Es el camino de desarrollo y tiene que
  seguir andando sin clave.
- Encabezado de una versión de formato desconocida: se rechaza en vez de adivinar.
- Nonce distinto en dos cifrados del mismo archivo con la misma clave.

---

## 10. Cómo se relaciona con la licencia

Son subsistemas separados y **no deben acoplarse**. El cifrado no pregunta por la
licencia y la licencia no conoce los pesos.

Hay una decisión pendiente en el borde, que este plan **no** resuelve pero deja anotada:

> **`STATE_FOREIGN` hoy no impide arrancar.** `should_refuse_start()` cubre `absent` e
> `invalid`; una licencia de otra máquina cae en la política firmada (`warn` o `degrade`),
> así que una instalación copiada a un segundo equipo **arranca** y se degrada, en vez de
> negarse. Para la amenaza «el cliente compra otra Jetson y copia el proyecto», eso
> probablemente no es lo que se quiere.
>
> El cambio es agregar `STATE_FOREIGN` a `NO_LICENSE_STATES`, pero es una decisión
> comercial, no técnica: `foreign` también aparece cuando se reemplaza un equipo de forma
> legítima, y ahí negarse a arrancar convierte una falla en garantía en una parada dura
> hasta que llegue la reemisión. Decidirlo junto con el plazo de reemisión, no antes.

**Decidido por ahora: `STATE_FOREIGN` no frena el arranque.** El equipo con una licencia
de otra máquina arranca y aplica su política firmada, como hasta hoy; no se tocó
`NO_LICENSE_STATES`. Se revisa cuando haya un plazo de reemisión comprometido, que es el
dato que falta para que negarse a arrancar no deje a un cliente parado por una falla en
garantía.

---

## 11. Orden de trabajo

    0. Contestar la pregunta del §4 contra el framework real     hecho: carga desde memoria
    1. El formato y el cifrado, puros, con sus tests             encrypted_weights.py
    2. `_read_weights()` en AbstractModel + una implementación   el mock lee sus pesos
    3. La derivación de clave y el módulo generado en el build   model_key.py
    4. La herramienta de cifrado en el repo de firma             encargada, sin construir
    5. Documentar el reemplazo de modelo en un equipo entregado  docs/model_protection.md

El paso 4 es el que queda, y no es de este repo: el encargo completo está en
`model-protection-signer-repo.md`, escrito para copiarlo al repositorio `licensing` y
ejecutarlo ahí.

## 12. Lo que se decidió, y lo que queda

1. **El framework carga desde memoria** (§4). `_read_weights()` devuelve bytes y ninguna
   implementación recibe una ruta.
2. **`STATE_FOREIGN` no frena el arranque por ahora** (§10). Se revisa junto con el plazo
   de reemisión.
3. **Los pesos se descifran de una sola vez.** Medido con la prueba manual: ~1 GB/s y un
   pico de memoria igual al tamaño del modelo. Los modelos de estos proyectos no son
   grandes, así que partir el descifrado sería complejidad sin beneficio. Si algún día un
   `.engine` grande en una Jetson chica lo pide, se sube el byte de versión del formato.
4. **La metadata también se protege.** Los nombres de clase, el umbral y la tarea viajan
   cifrados adentro del propio archivo de pesos y le ganan al `config.yaml`, que es un
   archivo de texto al lado del ejecutable. Las dos únicas claves que no puede pisar son
   `path` y `type`: un archivo que declarara desde dónde se carga sería un archivo
   diciendo cómo abrirse.

Lo único que queda abierto es lo del punto 2, y no es técnico.
