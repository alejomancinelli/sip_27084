# Licencia del equipo

Cómo se pide, cómo se instala y cómo se renueva la licencia de una instalación. Es el
documento de la puesta en marcha y del soporte: quien atiende un llamado de «no me
arranca» lee esto.

**Estado: implementado y sin usar en producción.** El subsistema es `system/license/` y
está cableado en `main.py`, pero le faltan dos cosas para tener efecto: la clave pública
real en `system/license/public_key.py` y un build compilado. Desde el código fuente la
licencia **no se verifica** y el programa lo dice al arrancar.

## Qué hace la licencia

Ata un equipo entregado a la máquina para la que se emitió, y declara qué habilita:

| Entitlement | Qué pasa si no alcanza |
|---|---|
| `max_cameras` | Las cámaras que sobran no arrancan. La pantalla las muestra con «Sobre el cupo de la licencia» en vez de dejar un hueco |
| `features` | El pipeline cuyo feature no está habilitado no arranca. Un feature por pipeline |
| `expires_at` | Vencida. `null` es perpetua |
| huella | La licencia es de otro equipo y no vale acá |

Lo que **no** hace: impedir que alguien con acceso físico y root al equipo se salga con la
suya. Ningún esquema del lado del cliente hace eso. El objetivo es que copiar salga más
caro que comprar y que sea deliberado y no accidental; el piso real es el contrato.

## Los tres archivos

| Archivo | Dónde | Qué es |
|---|---|---|
| `license.lic` | raíz del programa, al lado de `config.yaml` | La licencia. Se instala una vez |
| `license_request.json` | donde lo guarde el operador | La solicitud. Viaja por mail |
| `data/license_state.bin` | lo escribe el programa solo | Estado de reloj. No se toca |

En un equipo entregado, «la raíz del programa» es la carpeta donde está el ejecutable.

## Pedir una licencia

El equipo genera la solicitud; el proveedor devuelve el `.lic`.

**Desde la pantalla** — pestaña *Diagnóstico → Licencia*, botón **«Exportar
solicitud…»**. Elige dónde guardarla (un pendrive sirve) y avisa en la misma pantalla.

**Sin pantalla** (equipo en gabinete, headless):

    python -m system.license request

Deja `license_request.json` en la raíz. `-o <ruta>` lo escribe en otro lado.

La solicitud lleva **hashes** de la huella de hardware, nunca los valores: se puede mandar
por mail sin cuidado. También lleva cuántas cámaras y qué features declara el config, que
es lo que el proveedor cruza contra lo que se vendió.

## Instalar la licencia recibida

**Desde la pantalla** — botón **«Instalar licencia…»**, se elige el `.lic` y listo. Se
verifica **antes** de copiarlo: si no vale, dice por qué y **no toca la licencia que ya
estaba**. Una renovación fallida no puede dejar al equipo peor que antes.

**Sin pantalla:**

    python -m system.license install <archivo.lic>

Para ver qué licencia tiene el equipo y por qué vale o no:

    python -m system.license status

## Qué se ve cuando algo anda mal

El estado sale por tres lados a la vez, y los tres dicen lo mismo:

- **La pantalla**, en *Diagnóstico → Licencia*: un chip con el estado y, debajo, el motivo
  en texto. Es lo que hay que leer por teléfono.
- **El log**, en `data/logs/`. En headless es la única forma de verlo.
- **El PLC**: el bit «licencia no válida» de la palabra de estado del sistema, el registro
  `license_days_remaining` con los días que faltan, y el reloj del equipo en
  `clock_epoch_s_high` / `clock_epoch_s_low`. Los nombres y direcciones están en el mapa generado
  (`docs/modbus_map.md`). Qué tiene que hacer el PLC con todo eso está más abajo, en
  «Hasta dónde llega la defensa del reloj».

`license_days_remaining` es el que sirve para **alarmar con anticipación**: una licencia
anual avisa treinta días antes en el log y en la pantalla, y el PLC puede alarmar con el
umbral que quiera. Una perpetua publica un número alto y constante; una vencida, sin
licencia o con el reloj movido publica 0.

## Los estados, y qué hacer con cada uno

| Estado | Qué pasó | Qué hacer |
|---|---|---|
| **Licencia válida** | Todo en orden | Nada |
| **Sin licencia** | No hay `license.lic` | Instalarlo. **El equipo no arranca en este estado** |
| **Licencia inválida** | La firma no cierra, el archivo está cortado o la firmó una clave que este programa no conoce | Pedir el archivo de nuevo. Si dice que no conoce la clave, el equipo necesita una versión más nueva del software |
| **Licencia de otro equipo** | La huella no corresponde, o el `project_id` no es el de esta instalación | Verificar que sea el archivo de esta máquina. Si se reemplazó hardware, pedir reemisión |
| **Licencia vencida** | Pasó `expires_at` | Renovar |
| **Reloj del equipo atrasado** | Ver abajo | Ver abajo |

## El reloj atrasado

Un vencimiento se compara contra el reloj del equipo, y en planta no hay internet ni
servidor de tiempo confiable. Para que atrasar el reloj no sea gratis, el programa guarda
el instante más alto que vio, firmado con la huella de la máquina. Si el reloj aparece más
atrás que eso, la licencia queda en **«Reloj del equipo atrasado»** y no se evalúa el
vencimiento: con la fecha movida, preguntar si venció no significa nada.

**Salta también sin que nadie toque nada.** Los casos normales son una pila de BIOS
agotada, una Jetson sin RTC que arranca antes de sincronizar la hora, o un equipo al que
le corrigieron la fecha hacia atrás por una razón legítima.

**Cómo se recupera:** poner la fecha correcta en el equipo, **borrar
`data/license_state.bin`** y volver a arrancar. El programa lo vuelve a crear solo. El
motivo que se muestra en pantalla incluye la ruta completa del archivo, así que no hace
falta saberla de memoria.

No hay comando para esto a propósito: en un equipo entregado no hay intérprete de Python
donde correrlo, y el archivo ya es borrable por cualquiera con acceso al equipo — el
esquema nunca pretendió otra cosa.

## Hasta dónde llega la defensa del reloj, y qué la completa

**Lo que el equipo puede hacer solo tiene un techo, y conviene decirlo con todas las
letras.** La secuencia «atrasar el reloj, borrar `data/license_state.bin`, arrancar» deja
el equipo funcionando con la fecha vieja: sin estado guardado no hay con qué comparar, la
fecha cae dentro de la vigencia y la licencia da por válida. No es una falla de
implementación, es la forma del problema: todo lo que el equipo guarda vive en un disco
que el administrador de ese equipo controla. Le pasa a cualquier esquema de licencias
offline.

Esto **sólo afecta a las licencias con vencimiento**. Una perpetua no tiene fecha que
esquivar y su atadura a la máquina no se ve tocada.

Lo que sí cierra el hueco está afuera del equipo, y son dos cosas que van en el programa
del PLC. **Las dos son requisitos de la integración**, no algo que el equipo pueda
imponer: el equipo publica los datos y el PLC decide qué hacer con ellos.

### 1. Enclavar el bit de licencia

El bit «licencia no válida» de la palabra de estado tiene que quedar **enclavado** en el
PLC la primera vez que se ve, y el reset tiene que estar reservado a personal autorizado
del proveedor —con la misma protección que cualquier alarma que no se borra sola—.

El motivo es simple: el equipo puede *levantar* la alarma pero no puede *borrarla*. Quien
manipule el equipo puede dejarlo publicando «todo bien» al minuto siguiente, pero no puede
volver atrás el registro que ya quedó del otro lado. Es el patrón de cualquier alarma de
seguridad de la línea, y el personal de planta ya lo conoce.

### 2. Comparar el reloj del equipo contra el del PLC

El equipo publica su reloj como **segundos desde 1970-01-01 UTC**, en dos registros.
**El PLC es el único reloj confiable que hay en una instalación sin internet**: no lo
administra quien administra el equipo de visión.

Un registro de Modbus es de 16 bits siempre, así que un valor de 32 viaja en dos. Lo que
el protocolo no define es el orden de las palabras, y ahí es donde fallan estas
integraciones:

| Registro | Contenido |
|---|---|
| `clock_epoch_s_high` (97 / 40097) | palabra alta, bits 31-16 |
| `clock_epoch_s_low` (98 / 40098) | palabra baja, bits 15-0 |

    epoch = (40097 * 65536) + 40098

Alta primero, en la dirección más baja. Si el PLC lo lee como DINT y da un número absurdo,
está invirtiendo las palabras: casi todos los drivers tienen un `word swap` para eso.
Leído como DINT **con signo** el valor da la vuelta en 2038; como unsigned llega a 2106.

El programa del PLC lo compara con su propio reloj y alarma si se apartan más de lo
razonable. **Un día de tolerancia alcanza y sobra**, y de paso se come cualquier
diferencia de huso horario, así que no hay zona horaria que configurar en ningún lado. Un
atraso de meses o de un año —que es lo que hace falta para estirar una licencia— salta al
instante. Para esquivarlo habría que mover también el reloj del PLC, que es otro sistema y
otro nivel de acceso.

Combinadas, las dos convierten un descuido de dos minutos en un cambio deliberado sobre
dos equipos, con registro permanente en el que no se tocó. Ninguna de las dos hace falta
para operar: son el piso técnico de lo que el contrato dice.

## Reemplazo de hardware

La huella se toma de varias fuentes —UUID y serial de la placa, serial del módulo en la
Jetson, serial del disco, MAC de cada placa de red— y la licencia exige que **algunas**
sigan coincidiendo, no todas. Un disco o una placa de red reemplazados no invalidan la
licencia.

Lo que sí la invalida es cambiar el equipo entero, que es exactamente el caso de una falla
en garantía. Ahí hace falta **reemitir**: se genera una solicitud nueva en el equipo nuevo
y se pide la licencia de reemplazo.

> **Procedimiento comercial de reemisión — a completar.** Quién autoriza, quién firma y en
> cuánto tiempo. Esto no es un detalle administrativo: sin un plazo comprometido, el que
> se queda sin producción es el cliente que pagó, y es la forma más común en que estos
> esquemas lastiman al proveedor en vez de al que copia.

## Para el que instala en fábrica

Antes de entregar el equipo:

1. Correr `python -m system.license fingerprint` y confirmar que aparezcan **al menos dos
   fuentes estables** (`board_uuid`, `board_serial` o `module_serial`). Con menos, un
   reemplazo de pieza puede dejar afuera al cliente.
2. Generar la solicitud, pedir la licencia y **dejarla instalada**.
3. Verificar con `python -m system.license status` que diga «Licencia válida».
4. Confirmar que `project.client` y `project.project_id` del `config.yaml` sean los de la
   licencia: no son un control de seguridad, pero desacuerdan si se mandó el archivo
   equivocado, y eso conviene verlo en el banco y no en la planta.
5. **Con licencia anual**, verificar que el programa del PLC enclave el bit de licencia y
   compare el reloj del equipo contra el suyo (ver «Hasta dónde llega la defensa del
   reloj»). Sin eso, el vencimiento se esquiva atrasando la fecha del equipo.

## Probar el comportamiento sin entregar nada

`manual_test/license/` simula un equipo compilado con su propio `config.yaml`: permite ver
qué arranca y qué no con cada licencia, adelantar el reloj sin tocar el del equipo y
comprobar que editar el `.lic` a mano lo invalida. El propio script explica cada escenario.
