# Mapa de registros Modbus

Generado por `python -m system.modbus.export_map` desde `system/modbus/register_map.yaml`. No editar a mano: los cambios se hacen en el YAML y se vuelve a generar.

Holding registers de sólo lectura (FC03), direccionamiento base-1: el registro N es el que el PLC lee como 4000N.

Los valores con escala viajan multiplicados por ella: un registro con escala x10 y valor 415 son 41,5 en su unidad.

| Registro | PLC | Nombre | Descripción | Unidad | Escala | Origen |
|---|---|---|---|---|---|---|
| 1 | 40001 | system_status_bitfield | Estado del sistema (bitfield) — un bit en 1 es una falla | - | - | health |
| 2 | 40002 | com_status_bitfield | Canales de comunicación (bitfield) — un bit en 1 es un canal andando | - | - | health |
| 3 | 40003 | pct_pellet_norm | Composición — pellet entero, sobre el total detectado | % | x10 | inference |
| 4 | 40004 | pct_desmenuzado_norm | Composición — desmenuzado, sobre el total detectado | % | x10 | inference |
| 5 | 40005 | pct_carga | Carga de la cinta respecto del ROI; pasa de 100 % si hay desborde | % | x10 | inference |
| 6 | 40006 | pct_pellet_norm_1h | Pellet — media de la última hora, sólo con material en la cinta | % | x10 | health |
| 7 | 40007 | pct_desmenuzado_norm_1h | Desmenuzado — media de la última hora, sólo con material en la cinta | % | x10 | health |
| 8 | 40008 | pct_carga_1h | Carga — media de la última hora, sobre todas las mediciones | % | x10 | health |
| 9 | 40009 | window_coverage_pct | Cuánto de la última hora se llegó a medir — valida el registro 8 | % | - | health |
| 10 | 40010 | material_present_pct | Cuánto de lo medido tenía material — valida los registros 6 y 7 | % | - | health |
| 11 | 40011 | confidence_pct | Confianza media de las detecciones; 0 sin detecciones, que no es baja confianza | % | - | inference |
| 12 | 40012 | inference_time_ms | Duración del último ciclo de inferencia | ms | - | inference |
| 51 | 40051 | camera_1_state_bitfield | Estado de la cámara 1 (bitfield); el bit 6 es lente sucio | - | - | health |
| 52 | 40052 | camera_1_temperature_c | Temperatura de la cámara 1; 0 cuando no hay lectura | °C | - | health |
| 53 | 40053 | camera_1_fps | Frames por segundo medidos de la cámara 1 | fps | - | health |
| 54 | 40054 | camera_1_illumination_pct | Brillo medio del frame de la cámara 1 | % | - | inference |
| 81 | 40081 | heartbeat | Heartbeat / watchdog — cambia en cada ciclo mientras la app viva | - | - | health |
| 82 | 40082 | gpio_inputs_bitfield | Entradas digitales (bitfield); bits 8-11 marcan falla de lectura | - | - | health |
| 83 | 40083 | gpio_outputs_bitfield | Salidas digitales (bitfield) — eco de lo comandado, no lectura del hardware | - | - | health |
| 84 | 40084 | cpu_usage_pct | CPU en uso | % | - | health |
| 85 | 40085 | gpu_usage_pct | GPU en uso | % | - | health |
| 86 | 40086 | ram_used_mb | RAM usada | MB | - | health |
| 87 | 40087 | disk_free_gb | Espacio libre en disco | GB | - | health |
| 88 | 40088 | cpu_temp_c | Temperatura de CPU | °C | - | health |
| 89 | 40089 | gpu_temp_c | Temperatura de GPU | °C | - | health |
| 90 | 40090 | power_w | Consumo estimado | W | - | health |
| 97 | 40097 | clock_epoch_s_high | Reloj del equipo, palabra alta (epoch UTC = alta*65536 + baja) | s | - | health |
| 98 | 40098 | clock_epoch_s_low | Reloj del equipo, palabra baja | s | - | health |
| 99 | 40099 | license_days_remaining | Días hasta el vencimiento de la licencia | días | - | health |
