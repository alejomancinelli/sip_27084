"""
Prueba manual del servidor Modbus RTU sobre RS-485.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\modbus\\rtu_server.py
    Linux:    .venv/bin/python manual_test/modbus/rtu_server.py

Levanta el servidor sobre el `config.yaml` que tiene al lado —el mismo que usa
`tcp_server.py`, cada uno con su sección— y escribe los registros una vez por
segundo. La maquinaria está en `register_feeder.py`; acá sólo se elige el
transporte. Los valores son inventados: lo que se prueba es que el maestro los vea
cambiar.

A diferencia del TCP, esto **necesita un puerto serie**. Sin PLC a mano se arma un
par de puertos virtuales y se apunta cada punta a un lado:

    Windows: instalar com0com, crear el par COM10 <-> COM11.
             `modbus.rtu.port: COM10` en el config, y el cliente abre COM11.
    Linux:   socat -d -d pty,raw,echo=0 pty,raw,echo=0
             imprime los dos /dev/pts/N: uno va al config, el otro al cliente.

Con el par armado, el maestro es cualquier poller o el cliente de pymodbus. Los
parámetros de línea tienen que coincidir con los del config: si no, no hay
respuesta o llega con CRC malo.

    from pymodbus.client import ModbusSerialClient
    client = ModbusSerialClient("COM11", baudrate=115200, parity="N", stopbits=1)
    client.connect()
    client.read_holding_registers(80, count=9).registers   # 81-89: salud del equipo

Igual que en TCP, el mapa es base-1 y el cliente habla en direcciones de PDU: el
registro 81 —el 40081 del PLC— se pide como `address=80`.

Qué mirar:
    - Que el servidor arranque. En Windows el detalle que lo hace posible es la
      política de event loop: el default es Proactor, que no implementa
      `add_reader`, y sobre Proactor el RTU es imposible. `run()` fija
      WindowsSelectorEventLoopPolicy antes de crear el loop; si esa línea se
      pierde, esta prueba es la que se cae y el TCP sigue andando igual.
    - Con el puerto ocupado o inexistente, el RTU tiene que quedar en `error` con el
      motivo en el log —no un traceback— y la prueba cortar sola avisando que no
      quedó nada sirviendo, con código de salida 1. Sin `pyserial` instalado el
      motivo es ese y no el puerto: pymodbus no lo trae como dependencia dura.
    - El latido subiendo de uno en uno en el registro 81, igual que por TCP: es el
      mismo datastore, y verlo moverse por los dos cables es lo que comprueba que
      los transportes lo comparten.
    - La palabra de comunicaciones (registro 2) prende el bit 5, el de Modbus RTU, a
      los dos segundos del arranque. El bit 4 (TCP) tiene que quedar apagado: esta
      prueba apaga el TCP en memoria.
    - **Consultar con otro unit id no tiene que contestar nada.** Es la diferencia
      de fondo con el TCP: en un bus compartido, contestarle a una consulta dirigida
      a otro esclavo le pisa la respuesta y rompe la comunicación de los dos. El
      maestro tiene que ver el timeout del esclavo ausente, no un 0x0B nuestro.
    - Escrituras (FC06, FC16) y las demás lecturas (FC01, FC02, FC04): 0x02, igual
      que por TCP. El vocabulario no cambia con el transporte.
    - Bajar el `baudrate` a 9600 en el config y volver a correr: con muchos
      registros pedidos de una, la respuesta tarda visiblemente más. Sirve para
      dimensionar el ciclo de scan del PLC antes de prometerlo en una puesta en
      marcha.
    - Cortar con Ctrl+C: el puerto tiene que quedar liberado, y volver a arrancar la
      prueba no puede dar 'access denied' sobre el mismo COM.
"""

import sys

from register_feeder import TRANSPORT_RTU, serve

if __name__ == "__main__":
    sys.exit(serve(TRANSPORT_RTU))
