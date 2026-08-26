"""
Prueba manual del servidor Modbus TCP.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\modbus\\tcp_server.py
    Linux:    .venv/bin/python manual_test/modbus/tcp_server.py

Levanta el servidor sobre el `config.yaml` que tiene al lado —el mismo que usa
`rtu_server.py`, cada uno con su sección— y escribe los registros una vez por
segundo. La maquinaria está en `register_feeder.py`; acá sólo se elige el
transporte. Los valores son inventados: lo que se prueba es que el PLC los vea
cambiar.

No hace falta hardware. Alcanza con un cliente Modbus en el mismo equipo: un
poller (mbpoll, QModMaster, Modbus Poll) o el cliente que viene con pymodbus.

    from pymodbus.client import ModbusTcpClient
    client = ModbusTcpClient("127.0.0.1", port=5502)
    client.connect()
    client.read_holding_registers(80, count=9).registers   # 81-89: salud del equipo
    client.read_holding_registers(0, count=2).registers    # 1-2: palabras de estado

Ojo con el desfasaje que separa las dos numeraciones: el mapa es base-1 y el
cliente habla en direcciones de PDU, que arrancan en 0. El registro 81 —el 40081
del PLC— se pide como `address=80`. Es la resta que hace `update_register()` de
este lado.

Qué mirar:
    - El latido subiendo de uno en uno en el registro 81. Es lo único que el PLC
      necesita para saber que la app está viva; si se queda quieto con la prueba
      corriendo, el problema es el ciclo de escritura, no el transporte.
    - La palabra de comunicaciones (registro 2) arranca en 0 y a los dos segundos
      prende el bit 4, el de Modbus TCP. Ese es el margen que el servidor se da
      entre el bind y declararse `active`: no es un retardo del cliente.
    - El bit 5 (Modbus RTU) tiene que quedar apagado toda la corrida: esta prueba
      apaga el RTU en memoria, y verlo prendido sería el otro transporte
      levantando cuando no se lo pidió.
    - Pedir un rango que se pase del final del mapa —`address=95, count=10`— tiene
      que dar excepción 0x02 (illegal data address) y no un traceback en el log del
      servidor ni diez ceros que parezcan mediciones.
    - Pedir la misma lectura con otra función: FC01, FC02 y FC04 tienen que dar
      0x02. El mapa se sirve sólo por FC03.
    - Intentar escribir cualquier registro (FC06, FC16) también da 0x02: el mapa es
      de sólo lectura, la app escribe desde adentro.
    - Consultar con otro unit id —2 en vez del 1 del config— tiene que dar 0x0B
      (gateway target device failed to respond), no un timeout: en punto a punto
      conviene decirle al integrador que el id está mal.
    - Poner `port: 502` en el config: en Linux sin privilegios tiene que quedar en
      `error` con el motivo, y en Windows avisar de los rangos que reserva
      Hyper-V/WSL2.
    - Dejar el poller conectado y cortar la prueba con Ctrl+C: el servidor cierra
      sin dejar el puerto tomado, y volver a arrancarla no da 'address in use'.
"""

import sys

from register_feeder import TRANSPORT_TCP, serve

if __name__ == "__main__":
    sys.exit(serve(TRANSPORT_TCP))
