"""
Textos de la interfaz en los idiomas soportados.

Tabla de datos, no maquinaria: la clave es un identificador estable en inglés y el
valor es el texto que ve el operador en cada idioma. Ningún widget escribe un
literal visible: pide `tr("nav_monitor")` y el texto sale de acá.

El idioma sale de `ui.language` del config y se puede cambiar con `set_language()`,
que es lo que hace el panel de configuración al guardar. Un idioma que no está en
la tabla cae a `_FALLBACK_LANGUAGE` y lo avisa una sola vez; una clave que falta
devuelve la clave misma, para que el hueco se vea en pantalla en vez de romper la
vista.

Para agregar un idioma se suma su código a LANGUAGES y su entrada a cada texto. Un
texto sin traducir cae al idioma de respaldo, así que se puede traducir de a partes
sin dejar la UI a medio armar.
"""

from system.config_manager import ConfigManager
from system.logger import logger

LANGUAGES = ("es", "en", "pt")
_FALLBACK_LANGUAGE = "es"

# Clave -> texto por idioma. Agrupada por zona de la UI, no alfabéticamente: se
# edita mirando la pantalla.
_TEXTS: dict[str, dict[str, str]] = {
    # ── Ventana principal ────────────────────────────────────────────────────
    "nav_monitor":            {"es": "Monitor",        "en": "Monitor",       "pt": "Monitor"},
    "nav_config":             {"es": "Configuración",  "en": "Settings",      "pt": "Configuração"},
    "nav_diagnostics":        {"es": "Diagnóstico",    "en": "Diagnostics",   "pt": "Diagnóstico"},
    "nav_gpio":               {"es": "GPIO",           "en": "GPIO",          "pt": "GPIO"},
    "gpio_tooltip":           {"es": "Control de entradas/salidas digitales",
                               "en": "Digital input/output control",
                               "pt": "Controle de entradas/saídas digitais"},
    "config_loading":         {"es": "Cargando configuración…",
                               "en": "Loading settings…",
                               "pt": "Carregando configuração…"},
    "status_ready":           {"es": "Listo.",         "en": "Ready.",        "pt": "Pronto."},
    "status_started":         {"es": "Sistema inicializado.",
                               "en": "System initialised.",
                               "pt": "Sistema inicializado."},
    "status_config_saved":    {"es": "Configuración guardada.",
                               "en": "Settings saved.",
                               "pt": "Configuração salva."},
    # El id del proyecto y la versión del programa, abajo a la derecha. El separador va
    # en la tabla y no en el widget para que un proyecto pueda darlo vuelta.
    "footer_version":         {"es": "{project_id}  ·  v{version}",
                               "en": "{project_id}  ·  v{version}",
                               "pt": "{project_id}  ·  v{version}"},
    "footer_version_only":    {"es": "v{version}", "en": "v{version}", "pt": "v{version}"},

    # ── Monitor ──────────────────────────────────────────────────────────────
    "monitor_services":       {"es": "Estado de servicios",
                               "en": "Service status",
                               "pt": "Estado dos serviços"},
    "monitor_event_log":      {"es": "Log de eventos", "en": "Event log",     "pt": "Log de eventos"},
    "sidebar_tooltip":        {"es": "Mostrar u ocultar el panel lateral",
                               "en": "Show or hide the side panel",
                               "pt": "Mostrar ou ocultar o painel lateral"},

    # ── Panel y grilla de cámaras ────────────────────────────────────────────
    "camera_no_signal":       {"es": "SIN SEÑAL",      "en": "NO SIGNAL",     "pt": "SEM SINAL"},
    "camera_overlay_off":     {"es": "ANOTADO DESACTIVADO",
                               "en": "ANNOTATION OFF",
                               "pt": "ANOTACAO DESATIVADA"},
    "camera_pipeline_off":    {"es": "INFERENCIA DESACTIVADA",
                               "en": "INFERENCE OFF",
                               "pt": "INFERENCIA DESATIVADA"},
    "camera_waiting_inference": {"es": "ESPERANDO INFERENCIA",
                               "en": "WAITING FOR INFERENCE",
                               "pt": "AGUARDANDO INFERÊNCIA"},
    "camera_starting":        {"es": "Iniciando",      "en": "Starting",      "pt": "Iniciando"},
    "camera_connected":       {"es": "Conectada",      "en": "Connected",     "pt": "Conectada"},
    "camera_disconnected":    {"es": "Desconectada",   "en": "Disconnected",  "pt": "Desconectada"},
    "camera_disabled":        {"es": "Deshabilitada",  "en": "Disabled",      "pt": "Desabilitada"},
    "camera_misconfigured":   {"es": "Mal configurada",
                               "en": "Misconfigured",  "pt": "Mal configurada"},
    "camera_no_cameras":      {"es": "No hay cámaras configuradas.",
                               "en": "No cameras configured.",
                               "pt": "Nenhuma câmera configurada."},
    "camera_mode_raw":        {"es": "Video crudo",    "en": "Raw video",     "pt": "Vídeo cru"},
    "camera_mode_annotated":  {"es": "Video anotado",  "en": "Annotated video",
                               "pt": "Vídeo anotado"},
    "camera_show_all":        {"es": "Ver todas las cámaras",
                               "en": "Show all cameras",
                               "pt": "Ver todas as câmeras"},
    "camera_focus_tooltip":   {"es": "Ver todas las cámaras o una sola en grande",
                               "en": "Show all cameras, or just one enlarged",
                               "pt": "Ver todas as câmeras ou apenas uma ampliada"},

    # ── Configuración ────────────────────────────────────────────────────────
    "config_title":           {"es": "Configuración del sistema",
                               "en": "System settings",
                               "pt": "Configuração do sistema"},
    "config_save":            {"es": "Guardar y aplicar",
                               "en": "Save and apply",
                               "pt": "Salvar e aplicar"},
    "config_discard":         {"es": "Descartar",      "en": "Discard",       "pt": "Descartar"},
    "config_saved_title":     {"es": "Configuración guardada",
                               "en": "Settings saved",
                               "pt": "Configuração salva"},
    "config_saved_body":      {"es": "Los cambios se guardaron en el config.yaml.\n\n"
                                     "Reiniciá la aplicación para que tengan efecto.",
                               "en": "Changes were written to config.yaml.\n\n"
                                     "Restart the application for them to take effect.",
                               "pt": "As alterações foram salvas no config.yaml.\n\n"
                                     "Reinicie a aplicação para que tenham efeito."},
    "config_save_error":      {"es": "No se pudo guardar la configuración.",
                               "en": "Settings could not be saved.",
                               "pt": "Não foi possível salvar a configuração."},
    "config_restart_note":    {"es": "Los cambios toman efecto al reiniciar la aplicación.",
                               "en": "Changes take effect when the application restarts.",
                               "pt": "As alterações têm efeito ao reiniciar a aplicação."},

    "tab_cameras":            {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "tab_video":              {"es": "Video",          "en": "Video",         "pt": "Vídeo"},
    "tab_inference":          {"es": "Inferencia",     "en": "Inference",     "pt": "Inferência"},
    "tab_process":            {"es": "Proceso",        "en": "Process",       "pt": "Processo"},
    "tab_collector":          {"es": "Dataset",        "en": "Dataset",       "pt": "Dataset"},
    "tab_telemetry":          {"es": "Telemetría",     "en": "Telemetry",     "pt": "Telemetria"},
    "tab_modbus":             {"es": "Modbus",         "en": "Modbus",        "pt": "Modbus"},
    "tab_system":             {"es": "Sistema",        "en": "System",        "pt": "Sistema"},
    "tab_hardware":           {"es": "Hardware",       "en": "Hardware",      "pt": "Hardware"},
    "tab_logs":               {"es": "Logs",           "en": "Logs",          "pt": "Logs"},

    # ── Campos comunes ───────────────────────────────────────────────────────
    "field_enabled":          {"es": "Habilitado",     "en": "Enabled",       "pt": "Habilitado"},
    "field_host":             {"es": "Host:",          "en": "Host:",         "pt": "Host:"},
    "field_port":             {"es": "Puerto:",        "en": "Port:",         "pt": "Porta:"},
    "field_fps":              {"es": "FPS:",           "en": "FPS:",          "pt": "FPS:"},
    "field_name":             {"es": "Nombre:",        "en": "Name:",         "pt": "Nome:"},
    "field_url":              {"es": "URL:",           "en": "URL:",          "pt": "URL:"},
    "field_user":             {"es": "Usuario:",       "en": "User:",         "pt": "Usuário:"},

    # ── Cámaras (configuración) ──────────────────────────────────────────────
    "cam_box_connection":     {"es": "Conexión",       "en": "Connection",    "pt": "Conexão"},
    "cam_box_acquisition":    {"es": "Adquisición",    "en": "Acquisition",   "pt": "Aquisição"},
    "cam_box_roi":            {"es": "Región de interés (ROI)",
                               "en": "Region of interest (ROI)",
                               "pt": "Região de interesse (ROI)"},
    "cam_box_calibration":    {"es": "Calibración de lente",
                               "en": "Lens calibration",
                               "pt": "Calibração da lente"},
    "cam_enabled":            {"es": "Cámara habilitada",
                               "en": "Camera enabled",
                               "pt": "Câmera habilitada"},
    "cam_brand":              {"es": "Fabricante:",    "en": "Brand:",        "pt": "Fabricante:"},
    "cam_model":              {"es": "Modelo:",        "en": "Model:",        "pt": "Modelo:"},
    "cam_address":            {"es": "Dirección (IP o ID):",
                               "en": "Address (IP or ID):",
                               "pt": "Endereço (IP ou ID):"},
    "cam_rotation":           {"es": "Rotación:",      "en": "Rotation:",     "pt": "Rotação:"},
    "cam_fps_limit":          {"es": "Límite de FPS:", "en": "FPS limit:",    "pt": "Limite de FPS:"},
    "cam_exposure":           {"es": "Exposición (µs):",
                               "en": "Exposure (µs):",
                               "pt": "Exposição (µs):"},
    "cam_gain":               {"es": "Ganancia:",      "en": "Gain:",         "pt": "Ganho:"},
    "cam_illumination_min":   {"es": "Iluminación mínima (%):",
                               "en": "Minimum illumination (%):",
                               "pt": "Iluminação mínima (%):"},
    "cam_roi_enabled":        {"es": "ROI activo",     "en": "ROI active",    "pt": "ROI ativo"},
    "cam_roi_x":              {"es": "X (px):",        "en": "X (px):",       "pt": "X (px):"},
    "cam_roi_y":              {"es": "Y (px):",        "en": "Y (px):",       "pt": "Y (px):"},
    "cam_roi_width":          {"es": "Ancho (px):",    "en": "Width (px):",   "pt": "Largura (px):"},
    "cam_roi_height":         {"es": "Alto (px):",     "en": "Height (px):",  "pt": "Altura (px):"},
    "cam_roi_tool":           {"es": "Herramienta interactiva de ROI",
                               "en": "Interactive ROI tool",
                               "pt": "Ferramenta interativa de ROI"},
    "cam_calibration_note":   {"es": "Los intrínsecos del lente se editan en el config.yaml: "
                                     "son una matriz y una lista de coeficientes.",
                               "en": "Lens intrinsics are edited in config.yaml: they are a "
                                     "matrix and a list of coefficients.",
                               "pt": "Os intrínsecos da lente são editados no config.yaml: são "
                                     "uma matriz e uma lista de coeficientes."},
    "cam_calibration_set":    {"es": "Corrección de lente configurada",
                               "en": "Lens correction configured",
                               "pt": "Correção de lente configurada"},
    "cam_calibration_unset":  {"es": "Sin corrección de lente",
                               "en": "No lens correction",
                               "pt": "Sem correção de lente"},
    "rotation_none":          {"es": "Sin rotación",   "en": "No rotation",   "pt": "Sem rotação"},
    "rotation_90cw":          {"es": "90° horario",    "en": "90° clockwise", "pt": "90° horário"},
    "rotation_90ccw":         {"es": "90° antihorario",
                               "en": "90° counter-clockwise",
                               "pt": "90° anti-horário"},
    "rotation_180":           {"es": "180°",           "en": "180°",          "pt": "180°"},

    # ── Video (configuración) ────────────────────────────────────────────────
    "video_box_http":         {"es": "HTTP (MJPEG)",   "en": "HTTP (MJPEG)",  "pt": "HTTP (MJPEG)"},
    "video_box_rtsp":         {"es": "RTSP",           "en": "RTSP",          "pt": "RTSP"},
    "video_box_display":      {"es": "Ajuste de visualización",
                               "en": "Display adjustment",
                               "pt": "Ajuste de visualização"},
    "video_jpeg_quality":     {"es": "Calidad JPEG:",  "en": "JPEG quality:", "pt": "Qualidade JPEG:"},
    "video_frame_width":      {"es": "Ancho del frame (px, 0 = nativo):",
                               "en": "Frame width (px, 0 = native):",
                               "pt": "Largura do frame (px, 0 = nativo):"},
    "video_codec":            {"es": "Codec:",         "en": "Codec:",        "pt": "Codec:"},
    "video_net_interfaces":   {"es": "Interfaces de red (vacío = todas):",
                               "en": "Network interfaces (empty = all):",
                               "pt": "Interfaces de rede (vazio = todas):"},
    "video_log_connections":  {"es": "Loguear conexiones de clientes",
                               "en": "Log client connections",
                               "pt": "Registrar conexões de clientes"},
    "video_gamma":            {"es": "Gamma — brillo (menos de 1 aclara)",
                               "en": "Gamma — brightness (below 1 brightens)",
                               "pt": "Gama — brilho (abaixo de 1 clareia)"},
    "video_clahe_clip":       {"es": "Contraste local CLAHE",
                               "en": "CLAHE local contrast",
                               "pt": "Contraste local CLAHE"},
    "video_display_note":     {"es": "Los dos ajustes van sólo al camino de visualización —la UI y "
                                     "los streams—: el modelo y el dataset siguen recibiendo el "
                                     "frame crudo. El contraste local rescata una escena con una "
                                     "zona quemada y otra en sombra, y cuesta bastante más que "
                                     "el gamma.",
                               "en": "Both adjustments apply only to the display path —the UI and "
                                     "the streams—: the model and the dataset still get the raw "
                                     "frame. Local contrast rescues a scene with one blown-out "
                                     "area and one in shadow, and costs far more than gamma.",
                               "pt": "Os dois ajustes vão somente ao caminho de visualização —a UI "
                                     "e os streams—: o modelo e o dataset continuam recebendo o "
                                     "frame cru. O contraste local resgata uma cena com uma área "
                                     "estourada e outra na sombra, e custa bem mais que a gama."},

    # ── Inferencia (configuración) ───────────────────────────────────────────
    "inf_box_model":          {"es": "Modelo",         "en": "Model",         "pt": "Modelo"},
    "inf_box_pipeline":       {"es": "Pipeline",       "en": "Pipeline",      "pt": "Pipeline"},
    "inf_box_overlay":        {"es": "Anotado del resultado",
                               "en": "Result overlay",
                               "pt": "Anotação do resultado"},
    "inf_model_type":         {"es": "Tipo:",          "en": "Type:",         "pt": "Tipo:"},
    "inf_model_path":         {"es": "Pesos:",         "en": "Weights:",      "pt": "Pesos:"},
    "inf_min_confidence":     {"es": "Confianza mínima por detección (%):",
                               "en": "Minimum confidence per detection (%):",
                               "pt": "Confiança mínima por detecção (%):"},
    "inf_max_side":           {"es": "Redimensionar (px, 0 = nativo):",
                               "en": "Resize (px, 0 = native):",
                               "pt": "Redimensionar (px, 0 = nativo):"},
    "inf_class_names":        {"es": "Nombres de clase:",
                               "en": "Class names",
                               "pt": "Nomes de classe"},
    "inf_pipeline_cameras":   {"es": "Cámaras (coma, vacío = todas):",
                               "en": "Cameras (comma, empty = all):",
                               "pt": "Câmeras (vírgula, vazio = todas):"},
    "inf_pipeline_interval":  {"es": "Intervalo mín. (s, 0 = todos los frames):",
                               "en": "Min. interval (s, 0 = every frame):",
                               "pt": "Intervalo mín. (s, 0 = todos os frames):"},
    "inf_min_detections":     {"es": "Detecciones mínimas del resultado:",
                               "en": "Minimum detections per result:",
                               "pt": "Detecções mínimas do resultado:"},
    "inf_min_result_conf":    {"es": "Confianza mínima del resultado (%):",
                               "en": "Minimum result confidence (%):",
                               "pt": "Confiança mínima do resultado (%):"},
    "inf_draw_timestamp":     {"es": "Dibujar la hora","en": "Draw timestamp","pt": "Desenhar a hora"},
    "inf_overlay_note":       {"es": "Las cajas, las máscaras, las etiquetas y el recuadro del ROI "
                                     "van siempre: apagarlas deja un stream anotado que no muestra "
                                     "nada. Sus claves siguen en el config.yaml por si un proyecto "
                                     "necesita apagarlas.",
                               "en": "Boxes, masks, labels and the ROI outline are always drawn: "
                                     "turning them off leaves an annotated stream showing nothing. "
                                     "Their keys remain in config.yaml in case a project needs to "
                                     "disable them.",
                               "pt": "As caixas, as máscaras, as etiquetas e o contorno do ROI vão "
                                     "sempre: desligá-las deixa um stream anotado que não mostra "
                                     "nada. Suas chaves seguem no config.yaml caso um projeto "
                                     "precise desligá-las."},
    "inf_frames_per_cycle":   {"es": "Imágenes por medición:",
                               "en": "Frames per measurement:",
                               "pt": "Imagens por medição:"},
    "inf_cycle_interval":     {"es": "Espera entre mediciones (s):",
                               "en": "Wait between measurements (s):",
                               "pt": "Espera entre medições (s):"},
    "inf_capture_timeout":    {"es": "Tiempo máximo por imagen (s):",
                               "en": "Timeout per frame (s):",
                               "pt": "Tempo máximo por imagem (s):"},
    "inf_idle_cameras":       {"es": "Apagar la captura entre mediciones",
                               "en": "Stop capture between measurements",
                               "pt": "Desligar a captura entre medições"},
    "inf_cycle_note":         {"es": "Con 1 imagen por medición las otras tres no hacen "
                                     "nada. Apagar la captura libera la red, pero deja el "
                                     "video en vivo sin imagen hasta la medición siguiente.",
                               "en": "With 1 frame per measurement the other three do "
                                     "nothing. Stopping capture frees the network but "
                                     "leaves the live view blank until the next measurement.",
                               "pt": "Com 1 imagem por medição as outras três não fazem "
                                     "nada. Desligar a captura libera a rede, mas deixa o "
                                     "vídeo ao vivo sem imagem até a medição seguinte."},
    "inf_crop_to_roi":        {"es": "Anotar sólo el recorte del ROI",
                               "en": "Annotate only the ROI crop",
                               "pt": "Anotar apenas o recorte do ROI"},
    "inf_mask_style":         {"es": "Dibujo de la máscara:",
                               "en": "Mask drawing:",   "pt": "Desenho da máscara:"},
    "inf_mask_alpha":         {"es": "Opacidad del relleno:",
                               "en": "Fill opacity:",   "pt": "Opacidade do preenchimento:"},
    "inf_font_scale":         {"es": "Escala de fuente:",
                               "en": "Font scale:",    "pt": "Escala da fonte:"},
    "inf_font_scale_auto":    {"es": "automática",
                               "en": "automatic",      "pt": "automática"},
    "inf_thickness":          {"es": "Grosor de línea (px):",
                               "en": "Line thickness (px):",
                               "pt": "Espessura da linha (px):"},

    # ── Proceso (configuración) ──────────────────────────────────────────────
    "process_empty_note":     {"es": "Esta instalación todavía no declaró parámetros de "
                                     "proceso editables desde la pantalla.\n\n"
                                     "Los parámetros de lo que se mide —escalas de píxel, "
                                     "límites de carga, umbrales— son distintos en cada "
                                     "proyecto, así que el template no los trae: viven en la "
                                     "sección «process:» del config.yaml y hoy se editan ahí.\n\n"
                                     "Para ponerlos en esta pantalla se completa "
                                     "ui/views/config/process_tab.py, que tiene los dos "
                                     "patrones —un valor suelto y un mapa por cámara— en su "
                                     "docstring. Ver docs/ui.md.",
                               "en": "This installation has not declared any process "
                                     "parameters editable from the screen yet.\n\n"
                                     "The parameters of what gets measured —pixel scales, "
                                     "load limits, thresholds— differ in every project, so the "
                                     "template does not ship them: they live in the «process:» "
                                     "section of config.yaml and are edited there for now.\n\n"
                                     "To bring them to this screen, fill in "
                                     "ui/views/config/process_tab.py, whose docstring has both "
                                     "patterns —a plain value and a per-camera map—. "
                                     "See docs/ui.md.",
                               "pt": "Esta instalação ainda não declarou parâmetros de "
                                     "processo editáveis pela tela.\n\n"
                                     "Os parâmetros do que se mede —escalas de pixel, limites "
                                     "de carga, limiares— são diferentes em cada projeto, "
                                     "então o template não os traz: vivem na seção «process:» "
                                     "do config.yaml e hoje se editam ali.\n\n"
                                     "Para colocá-los nesta tela, complete "
                                     "ui/views/config/process_tab.py, cujo docstring tem os "
                                     "dois padrões —um valor solto e um mapa por câmera—. "
                                     "Ver docs/ui.md."},

    # ── Dataset (configuración) ──────────────────────────────────────────────
    "col_box_general":        {"es": "Recolección",    "en": "Collection",    "pt": "Coleta"},
    "col_box_dedup":          {"es": "Deduplicación de frames",
                               "en": "Frame deduplication",
                               "pt": "Deduplicação de frames"},
    "col_mode":               {"es": "Modo:",          "en": "Mode:",         "pt": "Modo:"},
    "col_mode_on_demand":     {"es": "A pedido",       "en": "On demand",     "pt": "Sob demanda"},
    "col_mode_interval":      {"es": "Por intervalo",  "en": "By interval",   "pt": "Por intervalo"},
    "col_interval_min":       {"es": "Intervalo mínimo (s):",
                               "en": "Minimum interval (s):",
                               "pt": "Intervalo mínimo (s):"},
    "col_interval_max":       {"es": "Intervalo máximo (s):",
                               "en": "Maximum interval (s):",
                               "pt": "Intervalo máximo (s):"},
    "col_max_images":         {"es": "Máximo de imágenes por cámara:",
                               "en": "Maximum images per camera:",
                               "pt": "Máximo de imagens por câmera:"},
    "col_min_free_space":     {"es": "Espacio libre mínimo (GB):",
                               "en": "Minimum free space (GB):",
                               "pt": "Espaço livre mínimo (GB):"},
    "col_save_original":      {"es": "Guardar el frame crudo",
                               "en": "Save the raw frame",
                               "pt": "Salvar o frame cru"},
    "col_save_annotated":     {"es": "Guardar el frame anotado",
                               "en": "Save the annotated frame",
                               "pt": "Salvar o frame anotado"},
    "col_save_json":          {"es": "Guardar el JSON de inferencia",
                               "en": "Save the inference JSON",
                               "pt": "Salvar o JSON de inferência"},
    "col_history_len":        {"es": "Historial comparado (guardados):",
                               "en": "Compared history (saves):",
                               "pt": "Histórico comparado (salvamentos):"},
    "col_hamming":            {"es": "Umbral Hamming (0–64):",
                               "en": "Hamming threshold (0–64):",
                               "pt": "Limiar de Hamming (0–64):"},
    "col_mode_note":          {"es": "Cambiar el modo exige reiniciar el recolector.",
                               "en": "Changing the mode requires restarting the collector.",
                               "pt": "Alterar o modo exige reiniciar o coletor."},

    # ── Telemetría (configuración) ───────────────────────────────────────────
    "tel_box_influxdb":       {"es": "InfluxDB",       "en": "InfluxDB",      "pt": "InfluxDB"},
    "tel_box_mqtt":           {"es": "MQTT",           "en": "MQTT",          "pt": "MQTT"},
    "tel_org":                {"es": "Organización:",  "en": "Organisation:", "pt": "Organização:"},
    "tel_bucket":             {"es": "Bucket:",        "en": "Bucket:",       "pt": "Bucket:"},
    "tel_topic_base":         {"es": "Base de tópicos:",
                               "en": "Topic base:",    "pt": "Base de tópicos:"},
    "tel_tls":                {"es": "TLS",            "en": "TLS",           "pt": "TLS"},
    "tel_influx_token_note":  {"es": "El token sale de INFLUXDB_TOKEN, del entorno o del .env.",
                               "en": "The token comes from INFLUXDB_TOKEN, in the environment or the .env file.",
                               "pt": "O token vem de INFLUXDB_TOKEN, do ambiente ou do .env."},
    "tel_mqtt_password_note": {"es": "La password sale de MQTT_PASSWORD, del entorno o del .env.",
                               "en": "The password comes from MQTT_PASSWORD, in the environment or the .env file.",
                               "pt": "A senha vem de MQTT_PASSWORD, do ambiente ou do .env."},

    # ── Modbus (configuración) ───────────────────────────────────────────────
    "mb_box_general":         {"es": "General",        "en": "General",       "pt": "Geral"},
    "mb_box_tcp":             {"es": "TCP",            "en": "TCP",           "pt": "TCP"},
    "mb_box_rtu":             {"es": "RTU (RS-485)",   "en": "RTU (RS-485)",  "pt": "RTU (RS-485)"},
    "mb_slave_id":            {"es": "Slave id (1–247):",
                               "en": "Slave id (1–247):",
                               "pt": "Slave id (1–247):"},
    "mb_register_count":      {"es": "Registros expuestos:",
                               "en": "Exposed registers:",
                               "pt": "Registradores expostos:"},
    "mb_map_extent":          {"es": "El mapa cargado usa hasta el registro {max_addr}.",
                               "en": "The loaded map uses up to register {max_addr}.",
                               "pt": "O mapa carregado usa até o registrador {max_addr}."},
    "mb_map_overflow":        {"es": "El mapa llega al registro {max_addr}, más allá de los "
                                     "{count} expuestos: esos registros no se publican.",
                               "en": "The map reaches register {max_addr}, past the {count} "
                                     "exposed: those registers are not published.",
                               "pt": "O mapa chega ao registrador {max_addr}, além dos {count} "
                                     "expostos: esses registradores não são publicados."},
    "mb_serial_port":         {"es": "Puerto serie:",  "en": "Serial port:",  "pt": "Porta serial:"},
    "mb_baudrate":            {"es": "Baudrate:",      "en": "Baud rate:",    "pt": "Baud rate:"},
    "mb_parity":              {"es": "Paridad:",       "en": "Parity:",       "pt": "Paridade:"},
    "mb_stop_bits":           {"es": "Bits de stop:",  "en": "Stop bits:",    "pt": "Bits de parada:"},
    "mb_map_note":            {"es": "El mapa de registros vive en "
                                     "system/modbus/register_map.yaml y no se edita desde acá.",
                               "en": "The register map lives in "
                                     "system/modbus/register_map.yaml and is not edited here.",
                               "pt": "O mapa de registradores vive em "
                                     "system/modbus/register_map.yaml e não se edita aqui."},

    # ── Sistema (configuración) ──────────────────────────────────────────────
    "sys_box_project":        {"es": "Proyecto",       "en": "Project",       "pt": "Projeto"},
    "sys_box_general":        {"es": "General",        "en": "General",       "pt": "Geral"},
    "sys_box_interface":      {"es": "Interfaz",       "en": "Interface",     "pt": "Interface"},
    "sys_box_monitor":        {"es": "Monitor de hardware",
                               "en": "Hardware monitor",
                               "pt": "Monitor de hardware"},
    "sys_client":             {"es": "Cliente:",       "en": "Client:",       "pt": "Cliente:"},
    "sys_project_id":         {"es": "Id de proyecto:","en": "Project id:",   "pt": "Id do projeto:"},
    "sys_app_name":           {"es": "Nombre de la app:",
                               "en": "Application name:",
                               "pt": "Nome da aplicação:"},
    "sys_device_id":          {"es": "Id del equipo:", "en": "Device id:",    "pt": "Id do equipamento:"},
    "sys_log_level":          {"es": "Nivel de log:",  "en": "Log level:",    "pt": "Nível de log:"},
    "sys_logs_path":          {"es": "Carpeta de logs:",
                               "en": "Logs folder:",   "pt": "Pasta de logs:"},
    "sys_dataset_path":       {"es": "Carpeta del dataset:",
                               "en": "Dataset folder:","pt": "Pasta do dataset:"},
    "sys_language":           {"es": "Idioma:",        "en": "Language:",     "pt": "Idioma:"},
    "sys_dark_mode":          {"es": "Modo oscuro",    "en": "Dark mode",     "pt": "Modo escuro"},
    "sys_disk_path":          {"es": "Partición reportada:",
                               "en": "Reported partition:",
                               "pt": "Partição reportada:"},
    "sys_net_interfaces":     {"es": "Interfaces medidas (vacío = todas):",
                               "en": "Measured interfaces (empty = all):",
                               "pt": "Interfaces medidas (vazio = todas):"},

    # ── Diagnóstico ──────────────────────────────────────────────────────────
    "diag_title":             {"es": "Diagnóstico del sistema",
                               "en": "System diagnostics",
                               "pt": "Diagnóstico do sistema"},
    "diag_box_cameras":       {"es": "FPS por cámara", "en": "FPS per camera","pt": "FPS por câmera"},
    "diag_box_network":       {"es": "Red (Mbps)",     "en": "Network (Mbps)","pt": "Rede (Mbps)"},
    "diag_chart_cpu":         {"es": "CPU [%]",        "en": "CPU [%]",       "pt": "CPU [%]"},
    "diag_chart_gpu":         {"es": "GPU [%]",        "en": "GPU [%]",       "pt": "GPU [%]"},
    "diag_chart_ram":         {"es": "RAM [MB]",       "en": "RAM [MB]",      "pt": "RAM [MB]"},
    "diag_chart_cpu_temp":    {"es": "Temp. CPU [°C]", "en": "CPU temp [°C]", "pt": "Temp. CPU [°C]"},
    "diag_modbus_table_note": {"es": "Holding registers que publica el equipo.",
                               "en": "Holding registers published by the device.",
                               "pt": "Holding registers publicados pelo equipamento."},
    "diag_modbus_register":   {"es": "Registro",       "en": "Register",      "pt": "Registrador"},
    "diag_modbus_desc":       {"es": "Descripción",    "en": "Description",   "pt": "Descrição"},
    "diag_modbus_value":      {"es": "Valor",          "en": "Value",         "pt": "Valor"},
    "diag_streams_http":      {"es": "HTTP — URLs de los streams",
                               "en": "HTTP — stream URLs",
                               "pt": "HTTP — URLs dos streams"},
    "diag_streams_rtsp":      {"es": "RTSP — URLs de los streams",
                               "en": "RTSP — stream URLs",
                               "pt": "RTSP — URLs dos streams"},
    "diag_streams_empty":     {"es": "Todavía no hay URLs publicadas.",
                               "en": "No URLs published yet.",
                               "pt": "Ainda não há URLs publicadas."},
    "diag_clients":           {"es": "Clientes",       "en": "Clients",       "pt": "Clientes"},
    "diag_log_all":           {"es": "Todos",          "en": "All",           "pt": "Todos"},
    "diag_log_clear":         {"es": "Limpiar",        "en": "Clear",         "pt": "Limpar"},
    "diag_log_time":          {"es": "Hora",           "en": "Time",          "pt": "Hora"},
    "diag_log_module":        {"es": "Módulo",         "en": "Module",        "pt": "Módulo"},
    "diag_log_level":         {"es": "Nivel",          "en": "Level",         "pt": "Nível"},
    "diag_log_message":       {"es": "Mensaje",        "en": "Message",       "pt": "Mensagem"},

    # ── Servicios ────────────────────────────────────────────────────────────
    "service_modbus_tcp":     {"es": "Modbus TCP",     "en": "Modbus TCP",    "pt": "Modbus TCP"},
    "service_modbus_rtu":     {"es": "Modbus RTU",     "en": "Modbus RTU",    "pt": "Modbus RTU"},
    "service_video_http":     {"es": "Video HTTP",     "en": "HTTP video",    "pt": "Vídeo HTTP"},
    "service_video_rtsp":     {"es": "Video RTSP",     "en": "RTSP video",    "pt": "Vídeo RTSP"},
    "service_influxdb":       {"es": "InfluxDB",       "en": "InfluxDB",      "pt": "InfluxDB"},
    "service_mqtt":           {"es": "MQTT",           "en": "MQTT",          "pt": "MQTT"},
    "status_disabled":        {"es": "Deshabilitado",  "en": "Disabled",      "pt": "Desabilitado"},
    "status_starting":        {"es": "Iniciando",      "en": "Starting",      "pt": "Iniciando"},
    "status_connecting":      {"es": "Conectando",     "en": "Connecting",    "pt": "Conectando"},
    "status_active":          {"es": "OK",             "en": "OK",            "pt": "OK"},
    "status_error":           {"es": "Error",          "en": "Error",         "pt": "Erro"},
    "status_unknown":         {"es": "Desconocido",    "en": "Unknown",       "pt": "Desconhecido"},

    # ── Diálogos ─────────────────────────────────────────────────────────────
    "roi_title":              {"es": "Definir ROI",    "en": "Define ROI",    "pt": "Definir ROI"},
    "roi_box_coords":         {"es": "Coordenadas (píxeles)",
                               "en": "Coordinates (pixels)",
                               "pt": "Coordenadas (pixels)"},
    "roi_hint":               {"es": "Arrastrá con el mouse sobre la imagen para dibujar el ROI.",
                               "en": "Drag on the image with the mouse to draw the ROI.",
                               "pt": "Arraste com o mouse sobre a imagem para desenhar o ROI."},
    "roi_reset":              {"es": "Restablecer",    "en": "Reset",         "pt": "Restabelecer"},
    "roi_save":               {"es": "Guardar ROI",    "en": "Save ROI",      "pt": "Salvar ROI"},
    "dialog_cancel":          {"es": "Cancelar",       "en": "Cancel",        "pt": "Cancelar"},
    "dialog_close":           {"es": "Cerrar",         "en": "Close",         "pt": "Fechar"},
    "gpio_title":             {"es": "Entradas y salidas digitales",
                               "en": "Digital inputs and outputs",
                               "pt": "Entradas e saídas digitais"},
    "gpio_box_inputs":        {"es": "Entradas digitales (DI)",
                               "en": "Digital inputs (DI)",
                               "pt": "Entradas digitais (DI)"},
    "gpio_box_outputs":       {"es": "Salidas digitales (DO) — click para conmutar",
                               "en": "Digital outputs (DO) — click to toggle",
                               "pt": "Saídas digitais (DO) — clique para comutar"},
    "gpio_simulated":         {"es": "Hardware GPIO no disponible — modo simulado.",
                               "en": "GPIO hardware unavailable — simulated mode.",
                               "pt": "Hardware GPIO indisponível — modo simulado."},
    "gpio_high":              {"es": "ALTO",           "en": "HIGH",          "pt": "ALTO"},
    "gpio_low":               {"es": "BAJO",           "en": "LOW",           "pt": "BAIXO"},
    "gpio_on":                {"es": "ON",             "en": "ON",            "pt": "ON"},
    "gpio_off":               {"es": "OFF",            "en": "OFF",           "pt": "OFF"},
}

_language = ""
_warned_languages: set = set()


def get_language() -> str:
    """Idioma activo. En el primer llamado sale de `ui.language` del config."""
    global _language
    if not _language:
        set_language(str(ConfigManager().get("ui.language", _FALLBACK_LANGUAGE)))
    return _language


def set_language(language: str):
    """Fija el idioma activo. Uno no soportado cae al de respaldo y lo avisa."""
    global _language
    code = (language or "").strip().lower()
    if code not in LANGUAGES:
        if code not in _warned_languages:
            _warned_languages.add(code)
            logger.warning(
                f"[UI] Idioma '{language}' no soportado; se usa '{_FALLBACK_LANGUAGE}'. "
                f"Válidos: {', '.join(LANGUAGES)}."
            )
        code = _FALLBACK_LANGUAGE
    _language = code


def tr(key: str) -> str:
    """
    Texto de `key` en el idioma activo.

    Sin traducción al idioma activo cae al de respaldo; sin la clave devuelve la
    clave, para que el hueco se vea en pantalla y no rompa la vista.
    """
    texts = _TEXTS.get(key)
    if texts is None:
        logger.warning(f"[UI] Texto '{key}' no está en la tabla de idiomas.")
        return key
    return texts.get(get_language()) or texts.get(_FALLBACK_LANGUAGE, key)
