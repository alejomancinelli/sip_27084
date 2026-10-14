# Índice de tests

Generado por `python ci/suite_index.py` desde `test/`. No editar a mano: se regenera, y el hook de commit avisa si quedó desactualizado.

**68 archivos, 377 grupos, 1865 funciones de test.** pytest informa más casos porque cuenta cada parametrización por separado.

Cómo se corre (desde la raíz, con el intérprete del venv):

    .venv\Scripts\python.exe ci/check.py test                                  # toda la suite
    .venv\Scripts\python.exe ci/check.py test -- test/video/test_http_server.py  # un archivo
    .venv\Scripts\python.exe ci/check.py test -- -k "TestLifecycle"              # por nombre

## Por carpeta

| Carpeta | Archivos | Tests |
|---|---:|---:|
| [test/](#test) | 6 | 280 |
| [test/build/](#testbuild) | 2 | 43 |
| [test/camera/](#testcamera) | 3 | 151 |
| [test/ci/](#testci) | 2 | 22 |
| [test/formats/](#testformats) | 4 | 75 |
| [test/image_collector/](#testimage_collector) | 2 | 77 |
| [test/inference/](#testinference) | 11 | 300 |
| [test/inference/models/](#testinferencemodels) | 5 | 120 |
| [test/license/](#testlicense) | 8 | 138 |
| [test/modbus/](#testmodbus) | 3 | 138 |
| [test/telemetry/](#testtelemetry) | 1 | 31 |
| [test/telemetry/backends/](#testtelemetrybackends) | 3 | 85 |
| [test/tools/camera/](#testtoolscamera) | 5 | 119 |
| [test/tools/image/](#testtoolsimage) | 2 | 43 |
| [test/ui/](#testui) | 8 | 61 |
| [test/video/](#testvideo) | 3 | 182 |

## test/

### [test_config_manager.py](../test/test_config_manager.py) — 47 tests

Tests del ConfigManager: singleton, carga y fallback, rutas punteadas, aislamiento de los valores que entrega, guardado atómico y acceso concurrente.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestDefaultPath` |  | 2 |
| `TestSingleton` |  | 3 |
| `TestLoad` |  | 9 |
| `TestGet` |  | 8 |
| `TestSet` |  | 7 |
| `TestRevision` | Lo que un lector que cachea compara para saber si lo que guardó sigue valiendo. | 4 |
| `TestSave` |  | 9 |
| `TestThreadSafety` |  | 2 |
| `TestBackup` | El `.bak` que queda antes de pisar el archivo. | 3 |

<details><summary>Tests</summary>

- `TestDefaultPath`
  - `test_default_path_is_the_config_at_the_repo_root`
  - `test_default_path_does_not_depend_on_the_cwd`
- `TestSingleton`
  - `test_second_call_returns_the_same_instance`
  - `test_the_first_path_wins`
  - `test_state_is_shared_between_references`
- `TestLoad`
  - `test_valid_file_loads_without_fallback`
  - `test_load_returns_true_on_success`
  - `test_reload_picks_up_changes_on_disk`
  - `test_unusable_content_falls_back`
  - `test_missing_file_falls_back`
  - `test_unreadable_file_falls_back_instead_of_raising`
  - `test_the_fallback_mirrors_the_real_config_keys`
  - `test_a_good_reload_clears_the_fallback_state`
  - `test_a_failed_reload_does_not_keep_the_old_data`
- `TestGet`
  - `test_nested_value`
  - `test_an_explicit_null_is_not_the_default`
  - `test_unresolvable_path_returns_the_default`
  - `test_an_empty_path_does_not_return_the_whole_config`
  - `test_mutating_a_returned_dict_does_not_touch_the_config`
  - `test_mutating_a_returned_list_does_not_touch_the_config`
  - `test_a_missing_key_is_warned_once`
  - `test_a_reload_allows_warning_about_the_key_again`
- `TestSet`
  - `test_overwrites_an_existing_value`
  - `test_creates_the_missing_levels`
  - `test_fills_in_a_null_section`
  - `test_refuses_to_write_through_a_non_mapping`
  - `test_refuses_an_empty_path`
  - `test_does_not_touch_the_disk`
  - `test_stores_a_copy_of_the_value`
- `TestRevision`
  - `test_a_set_moves_it`
  - `test_a_reload_moves_it`
  - `test_reading_does_not_move_it`
  - `test_a_refused_set_does_not_move_it`
- `TestSave`
  - `test_saves_and_survives_a_reload`
  - `test_leaves_no_tmp_file_behind`
  - `test_keeps_the_key_order`
  - `test_the_yaml_comments_do_not_survive`
  - `test_a_value_that_is_not_plain_yaml_fails_without_touching_the_file`
  - `test_what_gets_saved_is_readable_again`
  - `test_an_unwritable_path_returns_false`
  - `test_the_fallback_does_not_overwrite_an_existing_file`
  - `test_the_fallback_bootstraps_a_missing_file`
- `TestThreadSafety`
  - `test_concurrent_writers_and_saves_do_not_lose_keys`
  - `test_iterating_what_get_returns_is_safe_while_another_thread_writes`
- `TestBackup`
  - `test_it_keeps_the_previous_version`
  - `test_it_holds_one_version_and_not_a_history`
  - `test_the_first_save_without_a_file_leaves_no_backup`

</details>

### [test_env.py](../test/test_env.py) — 13 tests

Tests del cargador del `.env`.

| Grupo | Tests |
|---|---:|
| `TestParsing` | 8 |
| `TestPrecedence` | 2 |
| `TestMissingFile` | 2 |
| `TestSecrecy` | 1 |

<details><summary>Tests</summary>

- `TestParsing`
  - `test_a_pair_reaches_the_environment`
  - `test_the_value_is_cut_at_the_first_equals`
  - `test_the_shapes_an_installer_writes`
  - `test_quotes_inside_the_value_survive`
  - `test_comments_and_blank_lines_are_ignored`
  - `test_a_line_without_equals_is_ignored`
  - `test_a_line_without_a_name_is_ignored`
  - `test_a_file_saved_from_notepad_still_parses`
- `TestPrecedence`
  - `test_the_process_environment_wins`
  - `test_the_rest_of_the_file_still_loads`
- `TestMissingFile`
  - `test_a_missing_file_is_not_an_error`
  - `test_a_directory_in_place_of_the_file_is_not_an_error`
- `TestSecrecy`
  - `test_the_values_never_reach_the_log`

</details>

### [test_gpio_control.py](../test/test_gpio_control.py) — 25 tests

Tests del controlador de GPIO: lectura del config, modo simulado, comando de salidas y estado de las entradas.

| Grupo | Tests |
|---|---:|
| `TestSimulatedMode` | 5 |
| `TestConfig` | 4 |
| `TestWithHardware` | 12 |
| `TestPollingThread` | 4 |

<details><summary>Tests</summary>

- `TestSimulatedMode`
  - `test_without_the_library_it_does_not_raise`
  - `test_disabled_in_the_config_is_not_an_error`
  - `test_enabled_without_the_library_reports_the_reason`
  - `test_commanding_an_output_still_succeeds`
  - `test_a_chip_that_cannot_be_opened_falls_back`
- `TestConfig`
  - `test_the_channel_is_the_number_at_the_end_of_the_key`
  - `test_a_key_without_a_number_is_ignored`
  - `test_an_unknown_output_is_refused`
  - `test_an_unknown_input_reads_as_an_error`
- `TestWithHardware`
  - `test_it_opens_one_request_for_outputs_and_one_for_inputs`
  - `test_commanding_an_output_writes_its_offset`
  - `test_the_initial_outputs_are_applied_on_open`
  - `test_reading_an_input_maps_the_active_value_to_one`
  - `test_reading_every_input_returns_one_entry_per_channel`
  - `test_a_write_that_fails_reports_false`
  - `test_a_read_that_fails_reports_the_error_value`
  - `test_a_failing_read_is_logged_once`
  - `test_a_recovered_read_is_logged_and_fails_loudly_again`
  - `test_closing_puts_the_outputs_at_rest_and_releases`
  - `test_closing_twice_is_harmless`
  - `test_it_reports_itself_as_active`
- `TestPollingThread`
  - `test_it_emits_the_state_of_every_input`
  - `test_an_interval_above_the_stop_check_is_respected`
  - `test_a_long_interval_does_not_delay_the_stop`
  - `test_the_interval_has_a_floor`

</details>

### [test_main.py](../test/test_main.py) — 77 tests

Tests del cableado: la salida del proceso y el manejo de señales.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestExit` |  | 3 |
| `TestUnlicensedCamera` | El status que el cableado publica por una cámara que la licencia deja afuera. | 3 |
| `TestUnlicensedCameraOnScreen` | Que la cámara fuera de cupo llegue al widget del monitor. | 3 |
| `TestInterruptSignals` |  | 3 |
| `TestModbusServerWiring` | Que el motivo por el que el mapa no cargó llegue al servidor. | 3 |
| `TestInferenceFields` | Los fields de un punto de telemetría de inferencia. | 14 |
| `TestFramePath` | El frame de cámara no pasa entero por la GUI: la inferencia y el video crudo lo toman en el hilo de captura, y la GUI recibe sólo el último de cada cámara cuando puede. | 7 |
| `TestDatasetGating` | Con la recolección apagada, o sin esa cámara, el resultado no se serializa: corre por frame en la GUI. | 3 |
| `TestHourlyMeans` | Lo que entra a las ventanas de la hora con cada tick, y cómo se publica. | 7 |
| `TestLensInterval` |  | 4 |
| `TestLensDirtyBit` | Qué llega al bit de lente sucio de la palabra de estado de la cámara. | 3 |
| `TestLensCalibration` |  | 7 |
| `TestLegacyFieldTypes` | Los nombres y los tipos con los que cada serie ya está guardada en el bucket. | 10 |
| `TestGpioRegisters` |  | 5 |
| `TestGpioShutdown` |  | 1 |
| `TestHeadlessUi` |  | 1 |

<details><summary>Tests</summary>

- `TestExit`
  - `test_by_default_it_returns_instead_of_killing_the_process`
  - `test_hard_exit_terminates_without_unwinding`
  - `test_the_log_is_flushed_either_way`
- `TestUnlicensedCamera`
  - `test_it_uses_the_stable_keys_of_a_driver`
  - `test_it_reads_as_a_camera_that_cannot_operate`
  - `test_it_says_why`
- `TestUnlicensedCameraOnScreen`
  - `test_the_status_reaches_the_monitor_widget`
  - `test_a_widget_without_the_method_is_not_a_crash`
  - `test_headless_has_no_widget_to_push_to`
- `TestInterruptSignals`
  - `test_ctrl_c_and_sigterm_are_always_handled`
  - `test_ctrl_break_is_handled_where_it_exists`
  - `test_the_handler_schedules_the_quit_instead_of_running_it`
- `TestModbusServerWiring`
  - `test_without_a_map_the_server_refuses_to_serve`
  - `test_with_the_map_loaded_nothing_blocks_it`
  - `test_it_passes_the_error_that_registers_reports`
- `TestInferenceFields`
  - `test_the_cycle_dispersion_travels_untouched`
  - `test_nothing_gets_a_second_round_of_suffixes`
  - `test_the_frame_count_is_how_many_results_entered`
  - `test_the_process_metrics_keep_their_own_names`
  - `test_class_percentages_stay_integers`
  - `test_the_composition_is_an_integer_too`
  - `test_the_composition_of_a_second_adds_up_to_100_with_empty_frames`
  - `test_the_load_of_a_second_still_counts_the_empty_frames`
  - `test_the_composition_of_a_second_is_weighted_by_load`
  - `test_a_second_without_material_publishes_the_composition_as_zero`
  - `test_confidence_and_illumination_use_their_legacy_names`
  - `test_the_scalars_are_not_published_twice`
  - `test_a_discarded_result_is_counted_with_its_reason`
  - `test_confidence_only_averages_the_reliable_ones`
- `TestFramePath`
  - `test_an_uncycled_engine_gets_the_frame_in_the_capture_thread`
  - `test_a_cycled_engine_waits_for_the_gui`
  - `test_the_raw_stream_gets_the_frame_only_with_clients`
  - `test_the_gui_is_notified_once_while_a_frame_waits`
  - `test_the_gui_takes_the_latest_frame`
  - `test_after_the_gui_takes_it_the_next_frame_notifies_again`
  - `test_each_camera_has_its_own_mailbox`
- `TestDatasetGating`
  - `test_nothing_is_built_with_the_collector_off`
  - `test_the_result_goes_to_the_dataset_with_the_collector_on`
  - `test_the_telemetry_keeps_the_numbers_without_the_frames`
- `TestHourlyMeans`
  - `test_empty_frames_do_not_dilute_the_composition_of_the_tick`
  - `test_the_composition_of_the_hour_is_a_ratio_of_sums`
  - `test_the_composition_of_the_hour_adds_up_to_100`
  - `test_an_hour_without_material_publishes_zero`
  - `test_the_load_still_averages_the_empty_frames`
  - `test_an_empty_tick_counts_as_measured_but_without_material`
  - `test_unreliable_frames_feed_neither_window`
- `TestLensInterval`
  - `test_it_takes_the_interval_from_the_config`
  - `test_without_the_key_it_falls_back_to_the_default`
  - `test_an_absurdly_short_interval_is_clamped`
  - `test_a_zero_interval_reads_as_a_value_that_is_not_there`
- `TestLensDirtyBit`
  - `test_an_uncalibrated_camera_never_reports_a_dirty_lens`
  - `test_sharpness_below_the_threshold_reports_a_dirty_lens`
  - `test_a_camera_that_never_measured_does_not_report_one`
- `TestLensCalibration`
  - `test_it_does_not_write_anything_before_the_last_sample`
  - `test_the_last_sample_writes_the_reference_and_persists_it`
  - `test_it_records_the_conditions_it_calibrated_under`
  - `test_it_stamps_when_the_calibration_was_taken`
  - `test_the_new_reference_takes_effect_without_a_restart`
  - `test_it_tells_the_screen_to_reload_the_reference`
  - `test_calibrating_one_camera_leaves_the_others_alone`
- `TestLegacyFieldTypes`
  - `test_the_hardware_fields_keep_their_old_names`
  - `test_every_hardware_field_is_an_integer`
  - `test_the_thermal_zones_are_not_published`
  - `test_an_interface_without_a_label_still_gets_published`
  - `test_every_instantaneous_percentage_is_an_integer`
  - `test_confidence_and_illumination_are_integers`
  - `test_the_inference_time_stays_decimal`
  - `test_the_frame_count_is_an_integer`
  - `test_the_optics_fields_keep_their_names_and_types`
  - `test_a_camera_that_never_measured_publishes_only_the_fixed_fields`
- `TestGpioRegisters`
  - `test_both_words_say_there_is_no_gpio_without_the_library`
  - `test_the_inputs_word_carries_the_last_polled_reading`
  - `test_the_outputs_word_echoes_what_was_commanded`
  - `test_both_words_have_a_row_in_the_map`
  - `test_an_analyzer_metric_cannot_overwrite_them`
- `TestGpioShutdown`
  - `test_the_polling_stops_before_the_lines_are_released`
- `TestHeadlessUi`
  - `test_it_answers_everything_the_window_answers`

</details>

### [test_paths.py](../test/test_paths.py) — 12 tests

Tests de las dos raíces.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestRoots` |  | 3 |
| `TestProgramLocation` | De dónde sale `APP_DIR` compilado. `__compiled__` sólo existe adentro de un binario, así que acá se le pasa uno armado con los dos campos que se leen. | 3 |
| `TestResolve` |  | 4 |
| `TestAppFile` |  | 2 |

<details><summary>Tests</summary>

- `TestRoots`
  - `test_in_the_repo_both_roots_are_the_repo`
  - `test_the_environment_moves_the_data_and_not_the_program`
  - `test_an_empty_variable_is_not_a_path`
- `TestProgramLocation`
  - `test_from_sources_it_is_the_repo`
  - `test_standalone_it_is_the_executable_folder`
  - `test_accelerated_it_ignores_the_build_interpreter`
- `TestResolve`
  - `test_a_relative_path_hangs_off_the_data_root`
  - `test_an_absolute_path_is_left_alone`
  - `test_an_empty_value_falls_back_to_the_default`
  - `test_it_follows_the_data_root_and_not_the_program`
- `TestAppFile`
  - `test_a_bundled_asset_hangs_off_the_program`
  - `test_it_ignores_where_the_data_lives`

</details>

### [test_system_monitor.py](../test/test_system_monitor.py) — 106 tests

Tests del monitor de sistema: fuentes de sysfs de la Jetson, fuentes de Windows, cálculo del throughput de red y estabilidad del contrato de `get_metrics`.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestBestTemp` |  | 7 |
| `TestToMbps` |  | 6 |
| `TestParseNvidiaSmi` |  | 5 |
| `TestIsLoopback` |  | 2 |
| `TestThermalZones` |  | 7 |
| `TestTemperatures` |  | 8 |
| `TestWindowsTemperatures` | Las zonas ACPI no dicen qué miden, así que la elección es por temperatura. | 4 |
| `TestGpuUsage` |  | 9 |
| `TestPower` |  | 8 |
| `TestNvidiaSmi` |  | 10 |
| `TestParseWmiZones` |  | 5 |
| `TestWmiZones` | La temperatura de Windows sale de las zonas ACPI del firmware. | 6 |
| `TestDisk` |  | 5 |
| `TestNetThroughput` |  | 11 |
| `TestGetMetrics` |  | 13 |

<details><summary>Tests</summary>

- `TestBestTemp`
  - `test_an_exact_name_is_found`
  - `test_the_candidate_order_decides`
  - `test_an_exact_name_beats_a_partial_one`
  - `test_a_partial_name_matches_ignoring_case`
  - `test_the_partial_match_does_not_depend_on_the_insertion_order`
  - `test_no_match_is_zero`
  - `test_no_zones_is_zero`
- `TestToMbps`
  - `test_bytes_of_one_second_become_mbps`
  - `test_the_interval_is_divided_not_assumed`
  - `test_a_slow_link_does_not_collapse_to_zero`
  - `test_a_counter_reset_counts_zero`
  - `test_no_traffic_is_zero`
  - `test_a_zero_interval_does_not_divide`
- `TestParseNvidiaSmi`
  - `test_a_full_line_gives_the_three_metrics`
  - `test_a_field_the_gpu_does_not_report_is_left_out`
  - `test_extra_spaces_are_tolerated`
  - `test_a_line_with_other_fields_is_discarded`
  - `test_an_empty_line_is_discarded`
- `TestIsLoopback`
  - `test_loopback_names`
  - `test_real_interfaces`
- `TestThermalZones`
  - `test_the_zones_are_read_by_name_in_celsius`
  - `test_entries_that_are_not_zones_are_ignored`
  - `test_a_zone_without_temp_is_skipped`
  - `test_a_non_numeric_temp_is_skipped`
  - `test_a_disabled_sensor_is_discarded`
  - `test_windows_does_not_walk_sysfs`
  - `test_no_thermal_directory_is_empty`
- `TestTemperatures`
  - `test_the_jetson_zones_feed_both_temperatures`
  - `test_without_a_gpu_zone_the_gpu_reports_the_cpu`
  - `test_without_a_cpu_zone_the_soc_answers`
  - `test_a_linux_zone_with_no_known_name_is_zero`
  - `test_windows_has_no_temperature_source_without_zones`
  - `test_on_x86_linux_psutil_answers_for_the_cpu`
  - `test_the_sensor_order_of_preference_is_respected`
  - `test_an_unknown_sensor_is_not_reported`
- `TestWindowsTemperatures`
  - `test_the_hottest_acpi_zone_is_the_cpu_temperature`
  - `test_a_single_zone_answers`
  - `test_the_acpi_zone_is_not_reported_as_the_gpu`
  - `test_a_known_name_still_wins`
- `TestGpuUsage`
  - `test_the_node_publishes_per_mille`
  - `test_a_light_load_is_not_read_as_a_percentage`
  - `test_an_idle_gpu_is_zero`
  - `test_a_saturated_gpu_is_one_hundred`
  - `test_the_value_is_clamped`
  - `test_a_negative_value_is_clamped`
  - `test_no_node_reports_no_source`
  - `test_the_second_node_answers_when_the_first_is_absent`
  - `test_windows_has_no_sysfs_source`
- `TestPower`
  - `test_the_iio_node_publishes_milliwatts`
  - `test_the_first_channel_is_the_module_total`
  - `test_the_hwmon_node_publishes_microwatts`
  - `test_jetpack_6_publishes_voltage_and_current`
  - `test_a_channel_without_current_reports_no_source`
  - `test_no_rail_reports_no_source`
  - `test_an_unreadable_rail_reports_no_source`
  - `test_windows_has_no_rails`
- `TestNvidiaSmi`
  - `test_the_output_becomes_metrics`
  - `test_the_query_asks_for_parseable_csv`
  - `test_only_the_first_gpu_is_reported`
  - `test_empty_output_gives_no_metrics`
  - `test_without_the_binary_nothing_is_launched`
  - `test_a_failure_gives_no_metrics`
  - `test_it_stops_querying_after_repeated_failures`
  - `test_a_transient_failure_does_not_disable_the_source`
  - `test_the_probe_does_not_inherit_stdin`
  - `test_disabling_one_probe_does_not_disable_the_other`
- `TestParseWmiZones`
  - `test_deci_kelvin_becomes_celsius`
  - `test_every_zone_is_reported`
  - `test_a_zone_that_reports_nothing_is_discarded`
  - `test_a_non_numeric_value_is_skipped`
  - `test_output_without_pairs_gives_no_zones`
- `TestWmiZones`
  - `test_the_zones_come_from_the_query`
  - `test_the_query_uses_the_wmi_class_and_not_a_perfmon_counter`
  - `test_the_result_is_cached`
  - `test_the_cache_expires`
  - `test_a_failure_does_not_retry_before_the_ttl`
  - `test_linux_never_launches_powershell`
- `TestDisk`
  - `test_the_free_space_is_reported_in_gib`
  - `test_a_relative_path_hangs_off_the_installation_root`
  - `test_the_default_is_the_installation_root`
  - `test_an_absolute_path_is_used_as_is`
  - `test_the_real_partition_answers`
- `TestNetThroughput`
  - `test_the_first_call_has_no_interval_to_divide`
  - `test_the_second_call_reports_the_difference`
  - `test_the_interval_is_measured`
  - `test_a_counter_reset_does_not_report_a_negative_rate`
  - `test_each_interface_is_independent`
  - `test_loopback_is_left_out_by_default`
  - `test_the_config_selects_the_interfaces`
  - `test_an_interface_of_another_machine_is_skipped`
  - `test_an_interface_that_appears_starts_from_zero`
  - `test_an_interface_that_disappears_stops_being_reported`
  - `test_the_baseline_advances_on_every_call`
- `TestGetMetrics`
  - `test_the_real_machine_answers_every_key`
  - `test_the_keys_are_there_even_without_any_source`
  - `test_the_jetson_sources_feed_the_metrics`
  - `test_windows_falls_back_to_nvidia_smi_for_the_gpu`
  - `test_a_firmware_without_zones_reports_no_temperature`
  - `test_the_windows_acpi_zone_feeds_the_cpu_temperature`
  - `test_windows_without_an_nvidia_gpu_reports_zeros`
  - `test_the_zones_are_read_once_per_call`
  - `test_a_broken_source_does_not_drag_the_others`
  - `test_nothing_propagates_when_every_source_fails`
  - `test_a_source_that_keeps_failing_warns_once`
  - `test_the_disk_path_is_read_at_each_call`
  - `test_concurrent_calls_do_not_corrupt_the_state`

</details>

## test/build/

### [test_build.py](../test/build/test_build.py) — 21 tests

Tests de las opciones con las que se llama a Nuitka.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestExtraFlags` | Las opciones que suma cada fork. | 5 |
| `TestOptionalPackages` | Lo que la maquinaria necesita que Nuitka incluya a mano, y sólo donde está instalado. Va fuera de `_EXTRA_FLAGS`: el GPIO es del template, no de un fork. | 4 |
| `TestExclusions` |  | 2 |
| `TestPlatform` | Las propiedades y el icono del `.exe` son recursos de un binario PE. En la Jetson no hay dónde guardarlos, y el comando no los lleva. | 3 |
| `TestAcceleratedBuild` | La forma de Linux: se compila lo nuestro y lo de terceros viaja suelto. | 4 |
| `TestKeyModules` | Las claves entran al binario y se borran después. Una que queda en el disco cambia lo que la suite encuentra al importar, y no tiene por qué estar en un checkout. | 3 |

<details><summary>Tests</summary>

- `TestExtraFlags`
  - `test_the_template_adds_none`
  - `test_what_the_fork_declares_ends_up_in_the_command`
  - `test_they_come_before_the_exclusions`
  - `test_the_machinery_flags_are_still_there`
  - `test_the_accelerated_build_takes_them_too`
- `TestOptionalPackages`
  - `test_an_installed_package_is_included`
  - `test_a_missing_one_is_left_out`
  - `test_the_fork_block_stays_empty_either_way`
  - `test_the_accelerated_build_leaves_it_loose`
- `TestExclusions`
  - `test_each_excluded_package_gets_its_own_flag`
  - `test_excluding_nothing_is_the_default`
- `TestPlatform`
  - `test_windows_gets_the_executable_properties`
  - `test_linux_does_not`
  - `test_windows_is_standalone_and_linux_is_accelerated`
- `TestAcceleratedBuild`
  - `test_it_compiles_our_packages_and_nothing_else`
  - `test_cryptography_stays_loose`
  - `test_files_are_found_next_to_the_binary`
  - `test_the_system_python_packages_stay_out`
- `TestKeyModules`
  - `test_both_are_removed`
  - `test_without_them_there_is_nothing_to_remove`
  - `test_they_are_removed_even_if_the_build_does_not_finish`

</details>

### [test_make_release.py](../test/build/test_make_release.py) — 22 tests

Tests del armado del entregable: qué se copia y qué no.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestModelsDirectory` | La carpeta de los pesos viaja entera, porque su contenido lo decide cada fork. | 4 |
| `TestLinuxRelease` | El entregable de la Jetson: lanzadores en bash, accesos `.desktop` y verificador. | 10 |
| `TestSitePackages` | Lo de terceros del build acelerado: el venv probado, sin lo de compilar y testear. | 4 |
| `TestDesktopEntryId` |  | 2 |
| `TestFindExecutable` |  | 2 |

<details><summary>Tests</summary>

- `TestModelsDirectory`
  - `test_the_gitkeep_does_not_travel`
  - `test_a_fork_without_a_model_yet_is_not_a_failure`
  - `test_a_directory_that_is_not_there_says_so`
  - `test_the_models_directory_is_the_one_the_repo_ships`
- `TestLinuxRelease`
  - `test_the_launchers_exec_the_nuitka_binary`
  - `test_the_data_dir_lives_in_one_file`
  - `test_no_placeholder_is_left_unfilled`
  - `test_the_accelerated_launcher_imports_only_what_travels`
  - `test_the_standalone_launcher_sets_no_python_path`
  - `test_the_camera_check_looks_where_the_packages_travel`
  - `test_the_scripts_have_unix_line_endings`
  - `test_the_scripts_are_executable`
  - `test_the_shortcut_quotes_a_name_with_spaces_and_quotes`
  - `test_the_icon_travels_with_the_program`
- `TestSitePackages`
  - `test_the_build_and_test_packages_stay_behind`
  - `test_one_that_a_runtime_package_needs_travels`
  - `test_a_test_extra_does_not_keep_it`
  - `test_the_system_packages_travel_as_symlinks`
- `TestDesktopEntryId`
  - `test_accents_and_spaces_become_a_plain_slug`
  - `test_a_name_without_ascii_letters_still_gets_one`
- `TestFindExecutable`
  - `test_linux_names_the_binary_main_bin`
  - `test_a_dist_without_a_binary_says_so_with_an_empty_name`

</details>

## test/camera/

### [test_capture_scheduler.py](../test/camera/test_capture_scheduler.py) — 46 tests

Tests del ciclo de medición: permiso por frame, ronda entre cámaras, agregación de los N resultados, ciclo vacío, vencimiento y apagado de captura entre ciclos.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestTakeFrame` |  | 5 |
| `TestHasMeasurementCycle` | La regla que comparten el scheduler y la pestaña de dataset. | 5 |
| `TestPassThrough` |  | 4 |
| `TestAggregation` |  | 7 |
| `TestEmptyCycle` |  | 3 |
| `TestRound` |  | 5 |
| `TestCycleInterval` |  | 2 |
| `TestTimeout` |  | 5 |
| `TestIdleCameras` |  | 6 |
| `TestLifecycle` |  | 4 |

<details><summary>Tests</summary>

- `TestTakeFrame`
  - `test_it_opens_once_per_requested_frame`
  - `test_it_reopens_when_the_result_arrives`
  - `test_a_camera_outside_the_round_is_refused`
  - `test_an_unknown_pipeline_is_let_through`
  - `test_a_stopped_scheduler_refuses`
- `TestHasMeasurementCycle`
  - `test_without_cycle`
  - `test_one_pipeline_with_cycle_is_enough`
  - `test_a_disabled_pipeline_with_cycle_does_not_count`
  - `test_a_missing_value_is_one_frame`
  - `test_without_pipelines`
- `TestPassThrough`
  - `test_one_frame_per_cycle_is_not_cycled`
  - `test_it_always_lets_the_frame_through`
  - `test_every_result_comes_out_untouched`
  - `test_it_asks_for_nothing_about_the_cameras`
- `TestAggregation`
  - `test_the_cycle_closes_on_the_nth_frame`
  - `test_the_metrics_are_the_average_of_the_cycle`
  - `test_it_carries_the_dispersion_and_the_sample_count`
  - `test_the_clean_name_is_not_duplicated_by_the_mean`
  - `test_the_representative_carries_the_frames`
  - `test_an_invalid_frame_does_not_enter_the_average`
  - `test_the_original_result_is_not_mutated`
- `TestEmptyCycle`
  - `test_a_cycle_without_valid_frames_is_emitted`
  - `test_it_publishes_zeros_and_not_the_last_good_value`
  - `test_the_sample_count_is_zero_before_ever_measuring`
- `TestRound`
  - `test_it_serves_one_camera_at_a_time`
  - `test_each_camera_gets_its_own_cycle_result`
  - `test_the_round_starts_over_after_the_last_camera`
  - `test_a_result_from_another_camera_does_not_advance_the_cycle`
  - `test_no_declared_cameras_falls_back_to_all_of_them`
- `TestCycleInterval`
  - `test_the_next_round_waits_for_the_interval`
  - `test_the_round_opens_once_the_interval_passed`
- `TestTimeout`
  - `test_a_frame_that_never_returns_expires_on_tick`
  - `test_it_does_not_expire_before_the_timeout`
  - `test_a_camera_that_gives_nothing_closes_its_cycle`
  - `test_a_partial_cycle_keeps_what_it_collected`
  - `test_a_stopped_scheduler_does_not_tick`
- `TestIdleCameras`
  - `test_disabled_by_default_it_never_asks`
  - `test_only_the_camera_being_measured_captures`
  - `test_the_turn_moves_to_the_next_camera`
  - `test_nobody_captures_while_the_interval_runs`
  - `test_stopping_gives_the_cameras_back`
  - `test_it_only_asks_when_something_changed`
- `TestLifecycle`
  - `test_start_is_idempotent`
  - `test_stop_is_idempotent`
  - `test_stopping_drops_what_the_cycle_had_collected`
  - `test_a_meaningless_frame_count_is_not_a_cycle`

</details>

### [test_capture_thread.py](../test/camera/test_capture_thread.py) — 33 tests

Tests del hilo de captura: lectura de su propia config, conexión y reconexión, entrega de frames, telemetría sin retoques y parada limpia.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestConstruction` |  | 7 |
| `TestConfigError` | La cámara mal configurada no se reintenta a ciegas: se informa el motivo. | 6 |
| `TestStopWhileConnecting` | Con la cámara desconectada el hilo pasa casi todo el tiempo adentro de `connect()`. Si el cierre no lo despierta, el `wait()` se rinde con el hilo vivo y Qt aborta. | 1 |
| `TestConnection` |  | 5 |
| `TestFrames` |  | 5 |
| `TestDriverFailures` |  | 4 |
| `TestTelemetry` |  | 5 |

<details><summary>Tests</summary>

- `TestConstruction`
  - `test_the_thread_reads_its_own_camera_section`
  - `test_a_missing_slot_builds_the_driver_with_an_empty_config`
  - `test_the_slot_is_public_identity`
  - `test_synthetic_marker_comes_from_the_driver`
  - `test_a_real_driver_is_not_synthetic`
  - `test_status_is_available_before_starting`
  - `test_get_status_returns_a_copy`
- `TestConfigError`
  - `test_the_reason_comes_from_the_driver_status`
  - `test_a_valid_camera_has_no_config_error`
  - `test_a_driver_with_an_error_but_no_config_error_flag_is_not_a_config_error`
  - `test_connect_is_never_attempted`
  - `test_the_error_is_logged_once`
  - `test_the_status_keeps_carrying_the_error`
- `TestStopWhileConnecting`
  - `test_stopping_wakes_a_connect_that_is_still_waiting`
- `TestConnection`
  - `test_the_thread_connects_by_itself`
  - `test_a_failed_connect_is_retried_until_it_works`
  - `test_a_disconnected_camera_still_reports_status`
  - `test_the_driver_is_released_on_shutdown`
  - `test_a_long_retry_does_not_block_the_shutdown`
- `TestFrames`
  - `test_frames_travel_with_the_slot`
  - `test_a_driver_without_frames_emits_nothing`
  - `test_a_disabled_camera_polls_slowly`
  - `test_a_capturing_camera_polls_fast`
  - `test_an_unknown_capture_state_polls_fast`
- `TestDriverFailures`
  - `test_an_exception_in_get_frame_forces_a_reconnection`
  - `test_the_thread_survives_an_exception_in_get_frame`
  - `test_an_exception_in_get_status_does_not_kill_the_thread`
  - `test_a_status_that_fails_is_not_published`
- `TestTelemetry`
  - `test_the_status_travels_as_the_driver_built_it`
  - `test_the_measured_fps_is_not_recalculated`
  - `test_status_has_the_stable_keys_while_disconnected`
  - `test_the_last_status_is_the_one_that_was_emitted`
  - `test_telemetry_is_periodic`

</details>

### [test_lens_health.py](../test/camera/test_lens_health.py) — 72 tests

Tests de la detección de lente sucio: región de análisis, medición sobre frames sintéticos, ventana de nitidez, veredicto y aviso de condiciones cambiadas.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestGetAnalysisRegion` |  | 8 |
| `TestMeasure` |  | 8 |
| `TestGetSharpnessPct` |  | 4 |
| `TestSharpnessWindow` |  | 8 |
| `TestGetState` |  | 8 |
| `TestSummarizeCalibration` |  | 8 |
| `TestGetCurrentConditions` |  | 3 |
| `TestGetMismatchReason` |  | 10 |
| `TestPureModule` |  | 1 |
| `TestLensHealthMonitor` | Lo que se prueba acá es que el estado NO se comparta entre cámaras: es la falla que no da error, sólo un equipo que deja de avisar. | 14 |

<details><summary>Tests</summary>

- `TestGetAnalysisRegion`
  - `test_without_roi_the_region_is_the_whole_frame`
  - `test_a_disabled_roi_is_ignored`
  - `test_an_enabled_roi_is_honoured`
  - `test_zero_size_reaches_the_edge`
  - `test_a_roi_saved_with_another_resolution_is_clamped`
  - `test_a_roi_fully_outside_the_frame_falls_back_to_the_whole_frame`
  - `test_a_frame_that_cannot_be_measured_gives_none`
  - `test_to_dict_uses_the_same_keys_as_the_config`
- `TestMeasure`
  - `test_a_blurred_frame_measures_lower_than_a_sharp_one`
  - `test_a_frame_that_cannot_be_measured_gives_none`
  - `test_the_region_travels_with_the_measurement`
  - `test_luma_is_the_mean_gray_level`
  - `test_a_frame_narrower_than_the_analysis_width_is_not_scaled_up`
  - `test_a_wider_frame_is_reduced_to_the_analysis_width`
  - `test_a_grayscale_frame_is_accepted`
  - `test_measuring_only_a_dirty_corner_sees_it`
- `TestGetSharpnessPct`
  - `test_the_calibrated_variance_is_one_hundred_percent`
  - `test_half_the_calibrated_variance_is_half`
  - `test_a_peak_is_capped_instead_of_going_off_scale`
  - `test_without_something_to_compare_it_is_zero`
- `TestSharpnessWindow`
  - `test_an_empty_window_has_no_maximum`
  - `test_it_keeps_the_highest_sample`
  - `test_samples_older_than_the_window_are_dropped`
  - `test_it_empties_itself_when_the_capture_dies`
  - `test_a_non_finite_sample_is_ignored`
  - `test_the_window_is_not_complete_before_it_started`
  - `test_the_window_completes_one_period_after_the_first_cycle`
  - `test_the_clock_starts_on_the_first_cycle_even_without_samples`
- `TestGetState`
  - `test_without_calibration_it_is_unavailable`
  - `test_enough_sharpness_is_ok`
  - `test_the_threshold_is_inclusive`
  - `test_positive_evidence_does_not_wait_for_the_window`
  - `test_a_low_maximum_before_the_window_completes_is_unavailable`
  - `test_a_low_maximum_over_the_whole_window_is_the_alarm`
  - `test_without_samples_it_is_unavailable_and_not_the_alarm`
  - `test_an_uncalibrated_detector_never_raises_the_alarm`
- `TestSummarizeCalibration`
  - `test_without_samples_there_is_nothing_to_summarize`
  - `test_it_takes_the_median_and_not_the_mean`
  - `test_it_reports_how_many_samples_it_used`
  - `test_consistent_samples_show_no_dispersion`
  - `test_scattered_samples_show_it`
  - `test_a_single_sample_has_no_dispersion_to_report`
  - `test_without_lumas_it_still_summarizes_the_variance`
  - `test_the_summary_feeds_the_reference_back`
- `TestGetCurrentConditions`
  - `test_it_reports_every_condition_that_moves_the_variance`
  - `test_an_empty_camera_gives_a_complete_dict`
  - `test_null_values_from_the_config_become_zero`
- `TestGetMismatchReason`
  - `test_the_same_conditions_have_nothing_to_report`
  - `test_without_saved_conditions_there_is_nothing_to_compare`
  - `test_a_changed_exposure_is_named`
  - `test_the_camera_rounding_the_exposure_is_not_a_change`
  - `test_a_changed_gain_is_named`
  - `test_a_changed_rotation_is_named`
  - `test_a_changed_roi_is_named_with_both_rectangles`
  - `test_a_condition_missing_on_either_side_is_skipped`
  - `test_a_non_numeric_value_still_compares_as_a_change`
  - `test_it_reports_the_first_mismatch_and_not_a_list`
- `TestPureModule`
  - `test_it_only_imports_the_standard_library_and_opencv`
- `TestLensHealthMonitor`
  - `test_an_unknown_camera_is_unavailable`
  - `test_an_unknown_camera_still_gives_the_full_set_of_keys`
  - `test_each_camera_keeps_its_own_window`
  - `test_each_camera_keeps_its_own_reference`
  - `test_calibrating_one_camera_does_not_touch_the_others`
  - `test_each_camera_gets_its_own_roi`
  - `test_one_camera_going_down_does_not_take_the_others_with_it`
  - `test_a_dead_camera_empties_its_window_even_without_frames`
  - `test_discard_expired_ages_every_camera`
  - `test_it_remembers_which_cameras_it_has_seen`
  - `test_status_reports_sharpness_against_that_cameras_reference`
  - `test_an_uncalibrated_reference_is_clamped_to_zero`
  - `test_update_returns_the_measurement_it_took`
  - `test_concurrent_cameras_do_not_corrupt_it`

</details>

## test/ci/

### [test_check.py](../test/ci/test_check.py) — 10 tests

Tests del punto de entrada de las verificaciones: qué corre cada etapa y con qué intérprete.

| Grupo | Tests |
|---|---:|
| `TestStages` | 4 |
| `TestPytestArguments` | 4 |
| `TestInterpreter` | 2 |

<details><summary>Tests</summary>

- `TestStages`
  - `test_without_a_stage_it_lints_and_then_tests`
  - `test_lint_does_not_run_the_tests`
  - `test_a_failing_lint_stops_before_the_tests`
  - `test_the_index_is_checked_and_not_rewritten`
- `TestPytestArguments`
  - `test_after_the_separator_they_go_to_pytest`
  - `test_the_whole_suite_runs_in_parallel`
  - `test_a_single_file_does_not_start_workers`
  - `test_without_xdist_it_runs_serially`
- `TestInterpreter`
  - `test_another_interpreter_hands_over_to_the_venv`
  - `test_the_venv_interpreter_runs_in_place`

</details>

### [test_suite_index.py](../test/ci/test_suite_index.py) — 12 tests

Tests del índice de la suite: qué se cuenta, cómo se agrupa y cuándo se lo da por viejo.

| Grupo | Tests |
|---|---:|
| `TestParse` | 4 |
| `TestMarkdown` | 4 |
| `TestCheck` | 4 |

<details><summary>Tests</summary>

- `TestParse`
  - `test_classes_and_loose_tests_are_grouped`
  - `test_a_class_without_tests_is_left_out`
  - `test_a_parametrized_test_counts_once`
  - `test_only_test_files_are_collected`
- `TestMarkdown`
  - `test_the_root_folder_goes_first`
  - `test_the_totals_add_up`
  - `test_the_summary_column_needs_one_class_docstring`
  - `test_a_pipe_in_a_docstring_does_not_break_the_table`
- `TestCheck`
  - `test_a_missing_index_is_stale`
  - `test_a_fresh_index_passes`
  - `test_a_new_test_makes_it_stale`
  - `test_check_does_not_write`

</details>

## test/formats/

### [test_camera_health.py](../test/formats/test_camera_health.py) — 29 tests

Tests de la palabra de estado de cámara: exclusividad de los bits de adquisición, orden de severidad y el bit de lente sucio, que es independiente.

| Grupo | Tests |
|---|---:|
| `(sin clase)` | 1 |
| `TestBitLayout` | 5 |
| `TestAcquisitionBit` | 9 |
| `TestPack` | 7 |
| `TestSetDirtyLens` | 3 |
| `TestDescribe` | 4 |

<details><summary>Tests</summary>

- `(sin clase)`
  - `test_module_is_pure`
- `TestBitLayout`
  - `test_acquisition_bits_are_distinct`
  - `test_dirty_lens_is_outside_the_acquisition_group`
  - `test_acquisition_mask_covers_exactly_its_bits`
  - `test_word_mask_covers_every_described_bit`
  - `test_every_bit_has_a_description`
- `TestAcquisitionBit`
  - `test_healthy_camera_is_ok`
  - `test_synthetic_driver_reports_mock`
  - `test_connected_without_frames`
  - `test_fps_none_counts_as_no_frames`
  - `test_disconnected_camera`
  - `test_config_error_wins_over_everything`
  - `test_disabled_wins_over_disconnected_and_no_frames`
  - `test_disconnected_wins_over_no_frames`
  - `test_capture_enabled_defaults_to_true`
- `TestPack`
  - `test_exactly_one_acquisition_bit_is_always_set`
  - `test_word_is_never_zero`
  - `test_word_stays_inside_the_mask`
  - `test_dirty_lens_coexists_with_a_healthy_camera`
  - `test_dirty_lens_coexists_with_a_dead_camera`
  - `test_clean_lens_leaves_the_bit_down`
  - `test_is_keyword_only`
- `TestSetDirtyLens`
  - `test_sets_the_bit_without_touching_acquisition`
  - `test_is_idempotent`
  - `test_false_leaves_the_word_untouched`
- `TestDescribe`
  - `test_zero_is_reported_as_missing_data`
  - `test_lists_both_groups`
  - `test_unknown_bit_reports_the_raw_word`
  - `test_every_bit_describes_itself`

</details>

### [test_com_status.py](../test/formats/test_com_status.py) — 17 tests

Tests de la palabra de comunicaciones: un bit por canal, y sólo prende con el canal andando de verdad.

| Grupo | Tests |
|---|---:|
| `TestBitLayout` | 3 |
| `TestIsActive` | 2 |
| `TestPack` | 8 |
| `TestDescribe` | 4 |

<details><summary>Tests</summary>

- `TestBitLayout`
  - `test_bits_are_distinct`
  - `test_word_mask_covers_exactly_the_used_bits`
  - `test_every_bit_has_a_description`
- `TestIsActive`
  - `test_active_and_connected_are_the_only_true_values`
  - `test_everything_else_is_false`
- `TestPack`
  - `test_no_channel_reported_is_zero`
  - `test_each_channel_sets_only_its_own_bit`
  - `test_connected_also_sets_the_bit`
  - `test_disabled_reads_the_same_as_broken`
  - `test_starting_is_not_yet_active`
  - `test_every_channel_at_once_fills_the_mask`
  - `test_word_stays_inside_the_mask`
  - `test_is_keyword_only`
- `TestDescribe`
  - `test_zero_says_there_is_nothing_up`
  - `test_lists_the_active_channels`
  - `test_unknown_bit_reports_the_raw_word`
  - `test_every_bit_describes_itself`

</details>

### [test_gpio_status.py](../test/formats/test_gpio_status.py) — 13 tests

Tests de las dos palabras de GPIO: estado por canal, bits de falla de lectura y la marca de GPIO ausente.

| Grupo | Tests |
|---|---:|
| `TestInputs` | 8 |
| `TestOutputs` | 3 |
| `TestWordMask` | 2 |

<details><summary>Tests</summary>

- `TestInputs`
  - `test_an_active_channel_sets_its_state_bit`
  - `test_the_channels_are_numbered_from_one`
  - `test_every_channel_can_be_active_at_once`
  - `test_a_failed_read_sets_the_error_bit_and_not_the_state`
  - `test_an_unwired_channel_is_zero_without_an_error_bit`
  - `test_a_channel_out_of_range_is_ignored`
  - `test_without_hardware_it_marks_the_word`
  - `test_an_empty_read_is_an_empty_word`
- `TestOutputs`
  - `test_a_commanded_output_sets_its_bit`
  - `test_outputs_have_no_error_bits`
  - `test_without_hardware_it_marks_the_word`
- `TestWordMask`
  - `test_every_bit_it_can_set_is_inside_the_mask`
  - `test_the_word_fits_in_a_register`

</details>

### [test_system_status.py](../test/formats/test_system_status.py) — 16 tests

Tests de la palabra de estado del sistema: con cualquier bit en 1 las mediciones de proceso no son aptas para control.

| Grupo | Tests |
|---|---:|
| `TestBitLayout` | 3 |
| `TestPack` | 9 |
| `TestDescribe` | 4 |

<details><summary>Tests</summary>

- `TestBitLayout`
  - `test_bits_are_distinct`
  - `test_word_mask_covers_exactly_the_used_bits`
  - `test_every_bit_has_a_description`
- `TestPack`
  - `test_everything_fine_is_zero`
  - `test_model_not_loaded_is_negated`
  - `test_each_flag_sets_only_its_own_bit`
  - `test_flags_accumulate`
  - `test_word_stays_inside_the_mask`
  - `test_any_bit_means_the_measurements_are_not_trustworthy`
  - `test_a_bad_licence_is_not_trustworthy_either`
  - `test_is_keyword_only`
  - `test_every_flag_is_required`
- `TestDescribe`
  - `test_zero_is_ok`
  - `test_lists_the_active_bits`
  - `test_unknown_bit_reports_the_raw_word`
  - `test_every_bit_describes_itself`

</details>

## test/image_collector/

### [test_collector.py](../test/image_collector/test_collector.py) — 62 tests

Tests del recolector de dataset: los dos modos de operación, los filtros, el buffer circular y la guardia de disco.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestPerceptualHash` |  | 5 |
| `TestWriteImage` |  | 3 |
| `TestFreeSpace` |  | 1 |
| `TestOnDemandMode` |  | 12 |
| `TestCameraFilter` | `image_collector.cameras` elige qué cámaras se guardan; vacía = todas. | 8 |
| `TestSaveConditions` |  | 4 |
| `TestDedup` |  | 5 |
| `TestCircularBuffer` |  | 4 |
| `TestDiskGuard` |  | 4 |
| `TestIntervalMode` |  | 8 |
| `TestIntervalSchedule` |  | 4 |
| `TestStats` |  | 4 |

<details><summary>Tests</summary>

- `TestPerceptualHash`
  - `test_same_frame_hashes_identical`
  - `test_different_scenes_are_far_apart`
  - `test_brightness_drift_stays_close`
  - `test_accepts_grayscale_frame`
  - `test_hash_fits_in_64_bits`
- `TestWriteImage`
  - `test_creates_missing_folders`
  - `test_writes_path_with_accents`
  - `test_returns_false_when_encoding_fails`
- `TestFreeSpace`
  - `test_measures_through_missing_folders`
- `TestOnDemandMode`
  - `test_start_does_not_launch_a_thread`
  - `test_save_now_writes_the_three_variants`
  - `test_push_frame_saves_nothing_without_a_thread`
  - `test_disabled_collection_saves_nothing`
  - `test_nothing_to_save_returns_false`
  - `test_annotated_only_still_writes`
  - `test_json_carries_the_stable_keys`
  - `test_the_context_is_written_as_it_came`
  - `test_the_context_is_copied_at_the_call`
  - `test_inference_is_copied_at_the_call`
  - `test_unknown_mode_falls_back_to_on_demand`
  - `test_set_active_persists_to_config`
- `TestCameraFilter`
  - `test_an_empty_list_saves_every_camera`
  - `test_a_missing_key_saves_every_camera`
  - `test_save_now_skips_a_camera_off_the_list`
  - `test_push_frame_skips_a_camera_off_the_list`
  - `test_the_list_is_read_on_every_save`
  - `test_a_disabled_collector_skips_every_camera`
  - `test_a_value_that_is_not_a_list_saves_every_camera`
  - `test_the_stats_report_the_list`
- `TestSaveConditions`
  - `test_condition_not_met_skips_the_frame`
  - `test_every_condition_has_to_pass`
  - `test_condition_that_raises_skips_the_frame`
  - `test_conditions_see_an_empty_dict_without_inference`
- `TestDedup`
  - `test_duplicate_frame_is_skipped`
  - `test_different_scene_is_saved`
  - `test_disabled_dedup_saves_the_duplicate`
  - `test_history_is_per_camera`
  - `test_history_length_bounds_the_comparison`
- `TestCircularBuffer`
  - `test_oldest_save_is_evicted_at_the_cap`
  - `test_eviction_removes_the_three_variants`
  - `test_cap_is_per_camera`
  - `test_indexes_what_was_already_on_disk`
- `TestDiskGuard`
  - `test_refuses_to_save_with_nothing_to_evict`
  - `test_evicts_until_there_is_room`
  - `test_stats_report_the_guard`
  - `test_unmeasurable_disk_does_not_block_saving`
- `TestIntervalMode`
  - `test_start_and_stop_are_idempotent`
  - `test_saves_one_image_per_camera_per_tick`
  - `test_a_pushed_frame_is_saved_once`
  - `test_last_push_wins`
  - `test_inactive_collection_pushes_nothing`
  - `test_stop_discards_the_cached_frame`
  - `test_save_now_still_writes_immediately`
  - `test_filters_apply_in_interval_mode`
- `TestIntervalSchedule`
  - `test_equal_bounds_give_a_fixed_period`
  - `test_bounds_out_of_order_are_swapped`
  - `test_interval_has_a_floor`
  - `test_interval_is_read_fresh_every_time`
- `TestStats`
  - `test_has_the_stable_keys`
  - `test_counters_start_at_zero`
  - `test_last_save_is_stamped`
  - `test_reports_mode_and_running`

</details>

### [test_conditions.py](../test/image_collector/test_conditions.py) — 15 tests

Tests de las condiciones de guardado del recolector de dataset.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestModuleSurface` |  | 1 |
| `TestPredicateContract` | Vale para todas: el recolector las trata a todas igual. | 4 |
| `TestFieldAtLeast` |  | 5 |
| `TestFieldTruthy` |  | 3 |
| `TestMinConfidence` |  | 2 |

<details><summary>Tests</summary>

- `TestModuleSurface`
  - `test_every_factory_has_its_own_cases`
- `TestPredicateContract`
  - `test_returns_a_real_bool`
  - `test_empty_inference_does_not_raise`
  - `test_does_not_touch_the_inference`
  - `test_name_carries_the_values_it_was_built_with`
- `TestFieldAtLeast`
  - `test_compares_against_the_minimum`
  - `test_missing_field_counts_as_zero`
  - `test_zero_minimum_lets_everything_through`
  - `test_non_numeric_value_does_not_meet_the_minimum`
  - `test_numpy_scalar_is_accepted`
- `TestFieldTruthy`
  - `test_truthy_values_pass`
  - `test_falsy_values_do_not_pass`
  - `test_missing_field_does_not_pass`
- `TestMinConfidence`
  - `test_reads_the_confidence_field`
  - `test_only_the_confidence_pct_key_counts`

</details>

## test/inference/

### [test_analysis.py](../test/inference/test_analysis.py) — 42 tests

Tests de las agregaciones sobre detecciones y de la agregación de un ciclo de varios resultados: promedio, resumen con desvío y elección del frame representativo.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestFilterByConfidence` |  | 3 |
| `TestAverageConfidence` |  | 2 |
| `TestCountAndArea` |  | 3 |
| `TestClassFractions` |  | 3 |
| `TestCoverage` |  | 3 |
| `TestSummarizeMetrics` | El ciclo de N frames de un proyecto que responde con varias imágenes por medición: la media sola no dice si los N coincidieron. | 9 |
| `TestPickRepresentative` | El promedio no tiene imagen: cuando N frames dan un solo número, hay que elegir cuál de los N se guarda y se muestra. | 9 |
| `TestAverageMetrics` |  | 6 |
| `TestSummaryKeys` | Qué claves inventa `summarize_metrics`, para que el consumidor las separe. | 4 |

<details><summary>Tests</summary>

- `TestFilterByConfidence`
  - `test_it_keeps_what_reaches_the_threshold`
  - `test_the_threshold_is_inclusive`
  - `test_without_detections_it_returns_empty`
- `TestAverageConfidence`
  - `test_it_averages_the_scores`
  - `test_without_detections_it_is_zero`
- `TestCountAndArea`
  - `test_it_counts_by_class`
  - `test_it_adds_up_the_area_by_class`
  - `test_empty_input_gives_empty_maps`
- `TestClassFractions`
  - `test_it_splits_by_count`
  - `test_it_splits_by_area`
  - `test_without_detections_there_are_no_classes_to_report`
- `TestCoverage`
  - `test_it_measures_the_analyzed_area`
  - `test_it_saturates_at_one_hundred`
  - `test_an_invalid_area_gives_zero`
- `TestSummarizeMetrics`
  - `test_it_reports_mean_std_and_range`
  - `test_the_output_is_flat`
  - `test_a_single_sample_has_no_dispersion`
  - `test_the_sample_count_shows_a_short_cycle`
  - `test_invalid_results_are_left_out`
  - `test_it_ignores_what_cannot_be_summarized`
  - `test_without_results_only_the_count`
  - `test_it_does_not_summarize_what_it_already_summarized`
  - `test_it_accepts_a_generator`
- `TestPickRepresentative`
  - `test_it_picks_the_one_closest_to_the_mean`
  - `test_a_big_scale_key_does_not_outweigh_a_small_one`
  - `test_it_can_be_told_which_keys_matter`
  - `test_invalid_results_are_never_picked`
  - `test_without_valid_results_there_is_nothing_to_pick`
  - `test_with_no_dispersion_the_first_one_serves`
  - `test_results_without_metrics_still_give_one`
  - `test_a_key_missing_in_one_result_is_not_used`
  - `test_it_returns_the_result_itself_not_a_copy`
- `TestAverageMetrics`
  - `test_it_averages_key_by_key`
  - `test_a_key_missing_in_one_result_averages_over_the_rest`
  - `test_it_ignores_what_cannot_be_averaged`
  - `test_invalid_results_are_left_out`
  - `test_it_can_be_told_to_average_everything`
  - `test_without_results_it_is_empty`
- `TestSummaryKeys`
  - `test_the_sample_count_is_a_summary_key`
  - `test_every_statistic_is_recognised`
  - `test_a_process_metric_is_not`
  - `test_everything_summarize_metrics_adds_is_recognised`

</details>

### [test_annotations.py](../test/inference/test_annotations.py) — 21 tests

Tests de los annotators del proyecto: el rectángulo de cinta por cámara, el panel de composición y la composición de varios en uno.

| Grupo | Tests |
|---|---:|
| `TestBeltRoi` | 7 |
| `TestCompositionPanel` | 8 |
| `TestChain` | 6 |

<details><summary>Tests</summary>

- `TestBeltRoi`
  - `test_it_draws_the_four_sides_at_the_configured_place`
  - `test_each_camera_gets_its_own_rectangle`
  - `test_a_camera_without_a_rectangle_gets_nothing`
  - `test_a_rectangle_without_area_draws_nothing`
  - `test_it_draws_with_no_detections_and_no_metrics`
  - `test_an_empty_frame_is_ignored`
  - `test_it_names_itself_with_the_cameras_it_covers`
- `TestCompositionPanel`
  - `test_it_draws_when_there_are_metrics`
  - `test_a_result_without_metrics_draws_nothing`
  - `test_metrics_without_any_known_class_draw_nothing`
  - `test_it_draws_the_load_even_without_classes`
  - `test_an_empty_frame_is_ignored`
  - `test_it_names_itself_with_its_classes`
  - `test_each_class_row_uses_the_mask_color_of_its_index`
  - `test_without_a_palette_it_uses_the_overlay_default`
- `TestChain`
  - `test_it_runs_every_annotator`
  - `test_the_last_one_draws_on_top`
  - `test_every_annotator_leaves_its_mark`
  - `test_an_empty_chain_draws_nothing`
  - `test_none_entries_are_skipped`
  - `test_it_names_the_annotators_it_carries`

</details>

### [test_engine.py](../test/inference/test_engine.py) — 82 tests

Tests del hilo de inferencia: fan-in de varias cámaras, always-fresh por cámara, recorte del ROI, compuertas de validez, analyzer inyectado y parada limpia.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestConstruction` |  | 4 |
| `TestQueue` |  | 10 |
| `TestConfigCache` | El config se lee una vez, no por frame: cada `get` es un lock global y una copia. | 2 |
| `TestRoiAndIllumination` |  | 7 |
| `TestPreprocessor` | El frame de referencia es lo que devuelve el preprocessor: el modelo y el overlay miden y dibujan sobre la misma imagen, así que no pueden divergir. | 8 |
| `TestClassifier` |  | 9 |
| `TestValidity` |  | 9 |
| `TestLabels` |  | 4 |
| `TestAnalyzer` |  | 5 |
| `TestAnnotation` |  | 13 |
| `TestLifecycle` |  | 5 |
| `TestSynchronousProcess` | `process()` corre el ciclo del hilo sobre un frame suelto, sin arrancarlo. | 6 |

<details><summary>Tests</summary>

- `TestConstruction`
  - `test_the_slot_comes_from_the_pipeline`
  - `test_cameras_are_read_from_its_own_section`
  - `test_status_is_available_before_starting`
  - `test_status_reports_the_models_of_the_pipeline`
- `TestQueue`
  - `test_an_empty_frame_is_ignored`
  - `test_a_camera_outside_the_pipeline_is_rejected`
  - `test_without_a_declared_list_every_camera_is_accepted`
  - `test_a_disabled_pipeline_takes_no_frames`
  - `test_the_latest_frame_of_a_camera_wins`
  - `test_frames_of_different_cameras_coexist`
  - `test_the_frame_is_kept_without_a_copy`
  - `test_the_turn_rotates_between_cameras`
  - `test_the_interval_drops_frames_that_arrive_early`
  - `test_ignore_interval_forces_the_frame`
- `TestConfigCache`
  - `test_frames_after_the_first_do_not_read_the_config`
  - `test_a_change_takes_effect_on_the_next_frame`
- `TestRoiAndIllumination`
  - `test_the_pipeline_receives_the_cropped_roi`
  - `test_detections_come_back_in_camera_coordinates`
  - `test_a_zero_size_roi_reaches_the_border`
  - `test_a_disabled_roi_infers_the_whole_frame`
  - `test_a_roi_outside_the_frame_falls_back_to_the_whole_frame`
  - `test_illumination_is_measured_on_the_analyzed_region`
  - `test_a_dark_frame_never_reaches_the_model`
- `TestPreprocessor`
  - `test_it_receives_the_frame_and_the_camera_slot`
  - `test_the_model_sees_what_it_returned`
  - `test_the_result_carries_it_as_the_source_frame`
  - `test_the_roi_is_read_on_the_preprocessed_frame`
  - `test_illumination_is_measured_on_the_preprocessed_frame`
  - `test_a_failing_preprocessor_falls_back_to_the_camera_frame`
  - `test_an_empty_result_falls_back_to_the_camera_frame`
  - `test_without_a_preprocessor_the_camera_frame_is_measured`
- `TestClassifier`
  - `test_without_a_classifier_the_detections_pass_through`
  - `test_it_receives_the_camera_slot`
  - `test_it_receives_the_detections_in_reference_frame_space`
  - `test_what_it_drops_does_not_reach_the_result`
  - `test_what_it_drops_does_not_drag_the_confidence`
  - `test_what_it_drops_does_not_count_for_min_detections`
  - `test_the_class_it_stamps_travels_in_the_result`
  - `test_dropping_everything_leaves_no_detections`
  - `test_a_failing_classifier_does_not_break_the_result`
- `TestValidity`
  - `test_a_full_result_is_valid`
  - `test_without_a_loaded_model_nothing_is_measured`
  - `test_a_failing_stage_invalidates_the_result_without_killing_the_thread`
  - `test_too_few_detections_invalidate_the_result`
  - `test_low_average_confidence_invalidates_the_result`
  - `test_an_invalid_result_keeps_its_detections`
  - `test_confidence_is_the_average_of_the_detections`
  - `test_the_synthetic_marker_travels_in_the_result`
  - `test_invalid_results_are_counted`
- `TestLabels`
  - `test_the_verdict_of_the_pipeline_reaches_the_result`
  - `test_without_labels_the_result_has_none`
  - `test_a_frame_that_never_ran_has_no_labels`
  - `test_the_analyzer_can_read_the_labels`
- `TestAnalyzer`
  - `test_the_analyzer_fills_the_metrics`
  - `test_without_an_analyzer_the_metrics_stay_empty`
  - `test_an_invalid_result_is_not_analyzed`
  - `test_a_failing_analyzer_does_not_break_the_result`
  - `test_the_analyzer_sees_the_inference_time`
- `TestAnnotation`
  - `test_the_result_carries_an_annotated_frame`
  - `test_the_camera_frame_is_never_touched`
  - `test_the_config_can_turn_the_overlay_off`
  - `test_nothing_is_drawn_when_nobody_is_watching`
  - `test_the_cycle_frame_is_drawn_even_when_nobody_is_watching`
  - `test_the_cycle_frame_still_obeys_the_config`
  - `test_the_annotator_draws_over_the_standard_overlay`
  - `test_the_timestamp_is_drawn_over_the_annotator`
  - `test_without_a_timestamp_the_annotator_has_the_last_word`
  - `test_the_annotator_does_not_run_when_nothing_is_drawn`
  - `test_a_failing_annotator_keeps_the_standard_frame`
  - `test_the_annotator_also_sees_invalid_results`
  - `test_the_gate_receives_the_camera_slot`
- `TestLifecycle`
  - `test_the_thread_loads_and_unloads_the_pipeline`
  - `test_it_stops_with_frames_still_queued`
  - `test_it_serves_every_camera_of_the_pipeline`
  - `test_the_result_names_its_pipeline_and_camera`
  - `test_the_inference_time_is_measured`
- `TestSynchronousProcess`
  - `test_it_returns_the_result_without_starting_the_thread`
  - `test_it_runs_the_same_cycle_as_the_thread`
  - `test_it_does_not_emit_the_result`
  - `test_it_does_not_add_to_the_counters_of_the_thread`
  - `test_it_does_not_load_the_pipeline`
  - `test_it_refuses_to_run_while_the_thread_is_running`

</details>

### [test_hooks.py](../test/inference/test_hooks.py) — 7 tests

Tests de los hooks de la cinta: qué le entrega `build_hooks()` al motor desde `process:`.

| Grupo | Tests |
|---|---:|
| `TestBeltHooks` | 7 |

<details><summary>Tests</summary>

- `TestBeltHooks`
  - `test_it_returns_the_hooks_object`
  - `test_the_belt_does_not_filter_detections`
  - `test_every_declared_class_reaches_the_metrics`
  - `test_the_load_is_measured_against_the_declared_belt`
  - `test_the_belt_rectangle_is_drawn_on_its_camera`
  - `test_the_belt_rectangle_is_not_drawn_on_other_cameras`
  - `test_without_a_belt_rectangle_the_load_is_against_the_whole_frame`

</details>

### [test_metrics.py](../test/inference/test_metrics.py) — 17 tests

Tests de la cuenta del proceso: reparto por clase contado por unión, los dos denominadores, la composición normalizada y el refinamiento de fondo oscuro.

| Grupo | Tests |
|---|---:|
| `TestClassFractions` | 4 |
| `TestUnionCounting` | 3 |
| `TestLoad` | 4 |
| `TestComposition` | 2 |
| `TestRefinedMasksAreCounted` | 1 |
| `TestScalars` | 1 |
| `TestWithoutFrame` | 1 |
| `TestDefaultAnalyzer` | 1 |

<details><summary>Tests</summary>

- `TestClassFractions`
  - `test_a_class_covering_a_tenth_reports_ten_percent`
  - `test_a_declared_class_without_detections_reports_zero`
  - `test_the_rest_of_the_frame_is_reported_as_belt`
  - `test_a_full_frame_leaves_no_belt_in_sight`
- `TestUnionCounting`
  - `test_two_overlapping_detections_count_the_shared_pixels_once`
  - `test_where_two_classes_overlap_the_last_one_wins`
  - `test_a_detection_without_a_mask_does_not_count`
- `TestLoad`
  - `test_load_is_measured_against_the_belt_roi`
  - `test_an_overflow_goes_past_one_hundred`
  - `test_without_a_declared_roi_it_falls_back_to_the_frame`
  - `test_a_roi_without_area_falls_back_to_the_frame`
- `TestComposition`
  - `test_the_normalized_classes_add_up_to_one_hundred`
  - `test_an_empty_belt_has_no_composition`
- `TestRefinedMasksAreCounted`
  - `test_it_counts_the_mask_it_is_given`
- `TestScalars`
  - `test_confidence_and_time_travel_as_metrics`
- `TestWithoutFrame`
  - `test_a_result_without_a_frame_measures_nothing`
- `TestDefaultAnalyzer`
  - `test_it_counts_without_plant_parameters`

</details>

### [test_offline.py](../test/inference/test_offline.py) — 14 tests

Tests del runner offline: los pipelines del config corridos sobre una imagen suelta.

| Grupo | Tests |
|---|---:|
| `TestPipelines` | 7 |
| `TestLoading` | 3 |
| `TestPreprocessor` | 2 |
| `TestHooks` | 2 |

<details><summary>Tests</summary>

- `TestPipelines`
  - `test_an_image_goes_through_the_pipeline_like_in_the_app`
  - `test_the_overlay_is_always_drawn`
  - `test_the_mock_model_marks_the_result_as_synthetic`
  - `test_there_is_one_result_per_pipeline_that_takes_the_camera`
  - `test_a_disabled_pipeline_is_not_built`
  - `test_a_pipeline_only_takes_its_own_cameras`
  - `test_a_camera_no_pipeline_takes_gives_no_results`
- `TestLoading`
  - `test_without_loading_the_result_says_there_is_no_model`
  - `test_unloading_leaves_the_pipelines_without_model`
  - `test_the_status_reports_each_pipeline_and_its_models`
- `TestPreprocessor`
  - `test_the_camera_preprocessor_is_applied`
  - `test_an_image_that_is_already_a_reference_frame_skips_it`
- `TestHooks`
  - `test_the_project_analyzer_computes_the_metrics`
  - `test_the_project_annotator_draws_its_references`

</details>

### [test_overlay.py](../test/inference/test_overlay.py) — 45 tests

Tests del dibujado: qué respeta cada flag, qué colores usa y que el frame de cámara nunca se toque.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestClassColors` |  | 3 |
| `TestAnnotate` |  | 12 |
| `TestDrawLine` |  | 3 |
| `TestCropToRoi` | `crop_to_roi`: lo que se muestra es lo que se analizó, y nada se mapea de vuelta. | 4 |
| `TestMaskStyle` | Contorno y relleno son dueños de píxeles distintos. | 5 |
| `TestFontScale` | `font_scale: 0` calcula el tamaño del texto a partir del ancho del frame. | 4 |
| `TestTextPanelColours` | Un panel puede pintar cada línea de su color, y es lo que deja que el resumen de clases coincida con el color de las máscaras. Lo que no se declara cae en el del panel. | 4 |
| `TestTextPanelLayout` | Los pares `(etiqueta, valor)` van en dos columnas con los valores alineados a la derecha, y el aire entre líneas crece con el texto. | 2 |
| `TestTextThickness` | El grosor del trazo acompaña al tamaño: un texto grande con trazo de 1 px se ve pálido y roto, que es lo que pasaba después de escalarlo con el ancho del frame. | 3 |
| `TestOverlappingFill` | El relleno se pinta por unión de la clase, no por instancia. | 5 |

<details><summary>Tests</summary>

- `TestClassColors`
  - `test_the_palette_cycles`
  - `test_two_classes_get_different_colors`
  - `test_the_config_palette_wins`
- `TestAnnotate`
  - `test_it_returns_a_new_frame`
  - `test_the_camera_frame_is_never_touched`
  - `test_without_a_frame_there_is_nothing_to_annotate`
  - `test_a_gray_frame_comes_back_in_color`
  - `test_the_box_is_drawn`
  - `test_boxes_can_be_turned_off`
  - `test_a_detection_outside_the_frame_is_skipped`
  - `test_the_label_adds_pixels_over_the_box`
  - `test_the_mask_tints_the_inside_of_the_box`
  - `test_masks_can_be_turned_off`
  - `test_a_mask_that_does_not_match_its_bbox_is_ignored`
  - `test_the_roi_is_marked`
- `TestDrawLine`
  - `test_it_draws_across_the_frame`
  - `test_the_label_adds_pixels`
  - `test_an_empty_frame_is_ignored`
- `TestCropToRoi`
  - `test_it_returns_only_the_roi`
  - `test_without_a_roi_it_returns_the_whole_frame`
  - `test_it_does_not_frame_its_own_border`
  - `test_the_detections_move_with_the_crop`
- `TestMaskStyle`
  - `test_the_outline_leaves_the_inside_untouched`
  - `test_the_fill_tints_the_inside_without_hiding_it`
  - `test_the_outline_is_the_class_color_without_blending`
  - `test_the_fill_alone_has_no_sharp_edge`
  - `test_both_draws_the_two_of_them`
- `TestFontScale`
  - `test_a_configured_scale_is_respected`
  - `test_a_narrow_frame_gets_smaller_text`
  - `test_it_stays_inside_the_bounds`
  - `test_the_panel_fits_inside_the_frame`
- `TestTextPanelColours`
  - `test_each_line_takes_its_own_colour`
  - `test_a_line_without_a_colour_falls_back_to_the_panel`
  - `test_fewer_colours_than_lines_is_not_an_error`
  - `test_without_colours_the_panel_is_of_one_colour`
- `TestTextPanelLayout`
  - `test_the_values_end_in_the_same_column`
  - `test_the_space_between_lines_grows_with_the_text`
- `TestTextThickness`
  - `test_it_grows_with_the_scale`
  - `test_it_is_never_zero`
  - `test_the_default_scale_keeps_the_stroke_it_always_had`
- `TestOverlappingFill`
  - `test_ten_stacked_instances_look_like_one`
  - `test_where_two_classes_overlap_the_last_one_wins`
  - `test_the_tint_still_reaches_the_frame`
  - `test_two_classes_side_by_side_keep_their_own_colour`
  - `test_an_outline_is_still_drawn_per_instance`

</details>

### [test_pipeline.py](../test/inference/test_pipeline.py) — 32 tests

Tests de la maquinaria del pipeline: carga de las etapas por slot, tiempos por etapa, encadenado en cascada y el pipeline de una etapa de este proyecto.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestLoading` |  | 5 |
| `TestRunning` |  | 12 |
| `TestDarkBackground` | El postproceso del proyecto: sacar de cada máscara la cinta que el contorno se tragó. | 8 |
| `TestOverlapGoesToTheMostConfident` | Donde dos máscaras se pisan, el píxel es de la detección más confiable. | 2 |
| `TestClassNames` | Las clases son las de `CLASS_NAMES`, y el modelo tiene que declarar las mismas. | 5 |

<details><summary>Tests</summary>

- `TestLoading`
  - `test_it_loads_one_model_per_declared_slot`
  - `test_loading_twice_does_not_reload`
  - `test_a_stage_that_could_not_load_leaves_the_pipeline_unloaded`
  - `test_a_pipeline_without_stages_is_never_loaded`
  - `test_unload_releases_every_stage`
- `TestRunning`
  - `test_the_template_pipeline_runs_its_single_stage`
  - `test_a_stage_without_a_model_returns_nothing`
  - `test_each_stage_gets_its_own_time`
  - `test_the_times_are_those_of_the_last_run`
  - `test_a_cascade_hands_the_previous_detections_over`
  - `test_the_first_stage_gets_no_previous_detections`
  - `test_a_failing_stage_propagates_to_the_caller`
  - `test_a_stage_can_leave_a_frame_verdict`
  - `test_a_skipped_stage_leaves_the_verdict_that_skipped_it`
  - `test_a_verdict_does_not_survive_the_next_frame`
  - `test_the_labels_are_a_copy`
  - `test_a_synthetic_stage_marks_the_pipeline`
- `TestDarkBackground`
  - `test_dark_pixels_leave_the_mask`
  - `test_the_threshold_is_per_camera`
  - `test_a_class_without_a_threshold_is_untouched`
  - `test_a_zero_threshold_disables_it`
  - `test_without_thresholds_nothing_is_refined`
  - `test_without_passing_them_the_thresholds_come_from_process`
  - `test_a_mask_left_empty_is_dropped`
  - `test_the_area_matches_the_refined_mask`
- `TestOverlapGoesToTheMostConfident`
  - `test_the_detections_leave_from_least_to_most_confident`
  - `test_the_count_gives_the_overlap_to_the_most_confident`
- `TestClassNames`
  - `test_each_detection_is_named_by_its_index`
  - `test_a_model_that_declares_the_same_classes_loads`
  - `test_a_model_with_the_classes_swapped_does_not_load`
  - `test_a_model_with_other_classes_does_not_load`
  - `test_a_model_that_declares_nothing_loads`

</details>

### [test_preprocessing.py](../test/inference/test_preprocessing.py) — 7 tests

Tests del preprocessor del motor: la cadena que arma el frame de referencia.

| Grupo | Tests |
|---|---:|
| `TestChain` | 5 |
| `TestConfig` | 2 |

<details><summary>Tests</summary>

- `TestChain`
  - `test_with_both_the_geometry_goes_first`
  - `test_with_both_the_name_says_what_runs`
  - `test_with_only_the_lens_it_is_the_undistorter_itself`
  - `test_with_only_the_adjustment_it_is_the_adjustment_itself`
  - `test_with_neither_there_is_no_preprocessor`
- `TestConfig`
  - `test_the_adjustment_is_read_from_each_camera`
  - `test_without_cameras_there_is_no_preprocessor`

</details>

### [test_result.py](../test/inference/test_result.py) — 17 tests

Tests de la forma del resultado: completado del área y el centroide, traslado al espacio del frame de cámara y serialización.

| Grupo | Tests |
|---|---:|
| `TestDetection` | 7 |
| `TestOffsetDetections` | 3 |
| `TestInferenceResult` | 7 |

<details><summary>Tests</summary>

- `TestDetection`
  - `test_the_area_comes_from_the_bbox_when_it_is_not_given`
  - `test_a_given_area_is_respected`
  - `test_the_centroid_comes_from_the_bbox_when_it_is_not_given`
  - `test_a_given_centroid_is_respected`
  - `test_a_degenerate_bbox_has_no_area`
  - `test_the_mask_does_not_go_to_the_dict`
  - `test_the_dict_carries_plain_types`
- `TestOffsetDetections`
  - `test_it_moves_the_bbox_and_the_centroid`
  - `test_the_area_does_not_change`
  - `test_a_zero_offset_changes_nothing`
- `TestInferenceResult`
  - `test_the_count_follows_the_detections`
  - `test_a_fresh_result_is_valid`
  - `test_the_dict_is_serializable_without_frames`
  - `test_a_fresh_result_has_no_labels`
  - `test_the_labels_travel_in_the_dict`
  - `test_the_labels_of_the_dict_are_a_copy`
  - `test_the_confidence_key_is_the_one_the_save_conditions_read`

</details>

### [test_rolling.py](../test/inference/test_rolling.py) — 16 tests

Tests de la media móvil por ventana de tiempo: evicción, drenaje sin muestras y el porcentaje de ventana medida que valida la media.

| Grupo | Tests |
|---|---:|
| `TestMean` | 4 |
| `TestTick` | 2 |
| `TestFillPct` | 4 |
| `TestHasComposition` | 3 |
| `TestRatioPct` | 3 |

<details><summary>Tests</summary>

- `TestMean`
  - `test_without_samples_it_returns_none`
  - `test_it_averages_what_is_inside_the_window`
  - `test_a_sample_older_than_the_window_is_evicted`
  - `test_the_edge_of_the_window_is_exclusive`
- `TestTick`
  - `test_ticking_drains_the_window_without_new_samples`
  - `test_ticking_inside_the_window_keeps_the_samples`
- `TestFillPct`
  - `test_a_full_window_reports_one_hundred`
  - `test_a_half_measured_window_reports_half`
  - `test_it_is_capped_at_one_hundred`
  - `test_without_an_expected_rate_it_reports_zero`
- `TestHasComposition`
  - `test_nothing_detected_is_not_a_composition`
  - `test_any_detected_class_makes_one`
  - `test_without_classes_there_is_none`
- `TestRatioPct`
  - `test_it_rounds_to_an_integer_percentage`
  - `test_without_a_total_it_returns_zero`
  - `test_it_is_capped_at_one_hundred`

</details>

## test/inference/models/

### [test_abstract_model.py](../test/inference/models/test_abstract_model.py) — 22 tests

Tests de la lectura de pesos del contrato: `_read_weights()` y la metadata protegida.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestPlainWeights` |  | 2 |
| `TestProtectedWeights` |  | 9 |
| `TestFailuresLeaveTheReason` |  | 8 |
| `TestMockModelReadsItsWeights` | El mock no necesita pesos, pero si su sección declara `path` los lee por el mismo camino: es lo que deja probar el cifrado de punta a punta sin ningún framework. | 3 |

<details><summary>Tests</summary>

- `TestPlainWeights`
  - `test_plain_weights_load_without_any_key`
  - `test_plain_weights_leave_no_metadata`
- `TestProtectedWeights`
  - `test_protected_weights_open_with_the_key_of_the_build`
  - `test_the_metadata_wins_over_the_config`
  - `test_the_class_names_are_those_of_the_weights`
  - `test_without_names_in_the_weights_they_come_from_the_config`
  - `test_a_model_that_declares_no_names_has_none`
  - `test_the_config_still_answers_what_the_metadata_does_not_declare`
  - `test_the_metadata_cannot_move_the_path`
  - `test_a_task_mismatch_in_the_metadata_fails_before_the_first_frame`
  - `test_a_task_that_agrees_loads`
- `TestFailuresLeaveTheReason`
  - `test_without_a_key_the_reason_names_the_key`
  - `test_the_wrong_key_does_not_open_the_weights`
  - `test_a_flipped_bit_does_not_load`
  - `test_a_truncated_file_does_not_load`
  - `test_a_missing_file_names_the_path`
  - `test_an_empty_file_is_not_weights`
  - `test_a_section_without_path_says_so`
  - `test_a_second_read_forgets_the_metadata_of_the_first`
- `TestMockModelReadsItsWeights`
  - `test_it_loads_protected_weights_and_uses_their_class_names`
  - `test_it_does_not_load_when_the_weights_do_not_open`
  - `test_without_a_path_it_keeps_working_as_before`

</details>

### [test_encrypted_weights.py](../test/inference/models/test_encrypted_weights.py) — 19 tests

Tests del contenedor de pesos cifrados: el formato y lo que pasa cuando algo no cierra.

| Grupo | Tests |
|---|---:|
| `TestRoundTrip` | 5 |
| `TestConfidentiality` | 3 |
| `TestFailures` | 8 |
| `TestPlainWeights` | 3 |

<details><summary>Tests</summary>

- `TestRoundTrip`
  - `test_weights_survive_the_round_trip`
  - `test_metadata_survives_the_round_trip`
  - `test_bundle_is_marked_as_protected`
  - `test_metadata_is_empty_when_none_was_packed`
  - `test_container_declares_the_magic_and_the_version`
- `TestConfidentiality`
  - `test_the_weights_are_not_in_the_container_in_the_clear`
  - `test_the_metadata_is_not_in_the_container_in_the_clear`
  - `test_two_packs_of_the_same_weights_use_a_different_nonce`
- `TestFailures`
  - `test_the_wrong_key_fails_with_a_readable_reason`
  - `test_no_key_at_all_fails_with_its_own_reason`
  - `test_a_key_of_the_wrong_size_is_rejected`
  - `test_a_single_flipped_bit_does_not_open`
  - `test_a_flipped_bit_in_the_header_does_not_open_either`
  - `test_a_truncated_container_fails_as_a_format_error`
  - `test_an_unknown_format_version_is_rejected_instead_of_guessed`
  - `test_metadata_that_is_not_serializable_is_refused_when_packing`
- `TestPlainWeights`
  - `test_plain_weights_come_back_untouched_and_without_a_key`
  - `test_plain_weights_are_not_reported_as_encrypted`
  - `test_an_empty_file_is_not_a_container`

</details>

### [test_model_factory.py](../test/inference/models/test_model_factory.py) — 36 tests

Tests de la fábrica de modelos, el NullModel, el MockModel y los helpers de config del contrato.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestModelFactory` |  | 6 |
| `TestModelFactoryConfigErrors` | Una config inválida NO debe caer en el mock: detecciones sintéticas con estado 'cargado' le esconderían el error a la UI y al PLC. | 4 |
| `TestModelStatus` |  | 5 |
| `TestTaskGuard` | Un .engine de detección cargado como segmentador tiene que fallar al cargar, no adentro del postproceso con la máquina ya instalada. | 7 |
| `TestModelConfig` |  | 5 |
| `TestMockModel` |  | 9 |

<details><summary>Tests</summary>

- `TestModelFactory`
  - `test_mock_model_created_for_mock`
  - `test_model_type_is_normalized`
  - `test_model_is_abstract_subclass`
  - `test_the_model_keeps_its_slot_as_identity`
  - `test_registered_models_matches_what_the_factory_builds`
  - `test_the_factory_does_not_load_the_model`
- `TestModelFactoryConfigErrors`
  - `test_an_invalid_section_returns_a_null_model`
  - `test_the_null_model_never_loads_nor_detects`
  - `test_the_null_model_reports_why`
  - `test_the_error_survives_unload`
- `TestModelStatus`
  - `test_status_keys_are_stable`
  - `test_a_loaded_model_reports_loaded`
  - `test_unload_goes_back_to_unloaded`
  - `test_the_synthetic_marker_is_published`
  - `test_the_task_is_published`
- `TestTaskGuard`
  - `test_the_framework_vocabulary_is_understood`
  - `test_an_unknown_task_is_not_a_task`
  - `test_a_matching_task_passes`
  - `test_a_mismatch_leaves_the_model_in_error`
  - `test_metadata_that_says_nothing_cannot_disagree`
  - `test_the_guard_runs_inside_load`
  - `test_a_model_that_failed_the_guard_detects_nothing`
- `TestModelConfig`
  - `test_class_names_come_from_the_config`
  - `test_a_class_without_a_name_falls_back_to_its_index`
  - `test_the_model_applies_its_own_confidence_threshold`
  - `test_a_relative_path_is_anchored_to_the_repo_root`
  - `test_an_empty_path_stays_empty`
- `TestMockModel`
  - `test_it_detects_without_any_framework`
  - `test_the_detection_count_is_configurable`
  - `test_detections_fit_inside_the_frame`
  - `test_confidence_decreases_along_the_frame`
  - `test_masks_are_the_size_of_their_bbox`
  - `test_the_area_of_a_mask_is_smaller_than_its_bbox`
  - `test_without_masks_the_area_is_the_bbox`
  - `test_an_unloaded_model_detects_nothing`
  - `test_an_empty_frame_detects_nothing`

</details>

### [test_model_key.py](../test/inference/models/test_model_key.py) — 9 tests

Tests de la derivación de la clave de los pesos.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestWithoutGeneratedModule` |  | 2 |
| `TestDerivation` |  | 6 |
| `TestMalformedGeneratedModule` | Un módulo generado a medias se trata como ausente: derivar igual daría una clave que no abre nada, y el motivo que se leería mandaría a buscar el problema al lado equivocado. | 1 |

<details><summary>Tests</summary>

- `TestWithoutGeneratedModule`
  - `test_there_is_no_key`
  - `test_the_label_says_there_is_no_key`
- `TestDerivation`
  - `test_the_key_has_the_size_aes_256_needs`
  - `test_the_same_material_always_derives_the_same_key`
  - `test_another_salt_derives_another_key`
  - `test_another_fragment_derives_another_key`
  - `test_the_order_of_the_fragments_matters`
  - `test_the_label_comes_from_the_generated_module`
- `TestMalformedGeneratedModule`
  - `test_there_is_no_key`

</details>

### [test_yolo_seg_model.py](../test/inference/models/test_yolo_seg_model.py) — 34 tests

Tests del modelo de segmentación: guardas de `load()`, traducción de la salida de ultralytics a detecciones del contrato y vuelta de coordenadas cuando se reduce el frame.

| Grupo | Tests |
|---|---:|
| `TestLoad` | 20 |
| `TestPredict` | 10 |
| `TestMaxSide` | 2 |
| `TestBboxClipping` | 2 |

<details><summary>Tests</summary>

- `TestLoad`
  - `test_it_declares_segmentation`
  - `test_it_loads_and_reports_itself`
  - `test_protected_weights_stop_it_with_the_reason`
  - `test_weights_that_cannot_be_read_stop_it`
  - `test_a_task_disagreement_stops_it_before_the_first_frame`
  - `test_a_runtime_without_a_declared_task_is_not_a_disagreement`
  - `test_loading_twice_does_not_reload`
  - `test_without_the_framework_it_reports_the_reason`
  - `test_a_framework_that_crashes_on_import_reports_the_reason`
  - `test_matplotlib_is_kept_off_qt_before_ultralytics_is_imported`
  - `test_the_engine_is_opened_before_reporting_loaded`
  - `test_an_engine_that_does_not_infer_is_an_error_not_loaded`
  - `test_the_task_comes_from_the_engine_header_not_from_the_call`
  - `test_another_export_version_is_warned`
  - `test_weights_without_a_header_have_no_metadata`
  - `test_the_class_names_of_an_engine_come_from_its_header`
  - `test_the_class_names_of_a_checkpoint_come_from_the_model`
  - `test_an_engine_without_a_header_declares_no_names`
  - `test_names_with_a_gap_in_the_indices_are_not_guessed`
  - `test_unloading_leaves_it_ready_to_load_again`
- `TestPredict`
  - `test_without_loading_it_returns_nothing`
  - `test_an_empty_frame_returns_nothing`
  - `test_a_polygon_becomes_a_detection_with_its_mask`
  - `test_the_mask_is_cropped_to_the_bbox`
  - `test_the_class_name_comes_from_the_config`
  - `test_the_confidence_is_reported_on_a_hundred`
  - `test_the_threshold_is_handed_to_the_framework`
  - `test_a_detection_without_a_polygon_is_dropped`
  - `test_a_prediction_without_masks_returns_nothing`
  - `test_a_framework_error_does_not_propagate`
- `TestMaxSide`
  - `test_the_coordinates_come_back_in_the_frame_it_received`
  - `test_a_frame_that_already_fits_is_not_resized`
- `TestBboxClipping`
  - `test_a_polygon_outside_the_frame_is_dropped`
  - `test_a_polygon_that_pokes_out_is_clipped`

</details>

## test/license/

### [test_clock.py](../test/license/test_clock.py) — 12 tests

Tests del estado de reloj: qué pasa cuando el archivo falta, cuando lo editan y cuando alguien atrasa la fecha del equipo.

| Grupo | Tests |
|---|---:|
| `TestRoundTrip` | 5 |
| `TestTampering` | 3 |
| `TestRollback` | 4 |

<details><summary>Tests</summary>

- `TestRoundTrip`
  - `test_saving_creates_the_directory`
  - `test_what_is_saved_comes_back`
  - `test_the_microseconds_of_a_real_clock_survive`
  - `test_a_missing_file_is_not_an_error`
  - `test_an_unwritable_path_does_not_raise`
- `TestTampering`
  - `test_an_edited_timestamp_is_rejected`
  - `test_the_state_of_another_machine_is_rejected`
  - `test_a_truncated_file_is_rejected`
- `TestRollback`
  - `test_no_previous_state_is_not_a_rollback`
  - `test_the_clock_moving_forward_is_normal`
  - `test_a_clock_moved_back_a_year_is_a_rollback`
  - `test_an_ntp_correction_of_seconds_is_not_a_rollback`

</details>

### [test_fingerprint.py](../test/license/test_fingerprint.py) — 20 tests

Tests de la huella: qué se descarta, cómo se cuenta el N-de-M, y que leer el hardware real no pueda tumbar el arranque.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestHashValue` |  | 5 |
| `TestMatching` |  | 7 |
| `TestMachineKey` |  | 3 |
| `TestRunProbe` | Cómo se lanza la consulta al sistema, que es lo que decide si anda compilada. | 2 |
| `TestReadComponents` |  | 3 |

<details><summary>Tests</summary>

- `TestHashValue`
  - `test_same_value_and_source_gives_same_hash`
  - `test_same_value_under_two_sources_gives_different_hashes`
  - `test_case_and_padding_do_not_change_the_hash`
  - `test_trailing_nul_of_the_device_tree_is_ignored`
  - `test_factory_filler_is_not_an_identity`
- `TestMatching`
  - `test_all_sources_match`
  - `test_a_replaced_disk_still_reaches_the_quorum`
  - `test_a_missing_source_does_not_count`
  - `test_another_machine_does_not_match`
  - `test_quorum_is_exact_not_approximate`
  - `test_a_license_without_machine_lock_corresponds_to_no_machine`
  - `test_zero_quorum_with_components_does_not_match_anything`
- `TestMachineKey`
  - `test_same_components_give_the_same_key`
  - `test_different_components_give_different_keys`
  - `test_empty_components_still_give_a_usable_key`
- `TestRunProbe`
  - `test_the_probe_does_not_inherit_stdin`
  - `test_a_probe_that_cannot_run_is_a_warning_and_not_an_exception`
- `TestReadComponents`
  - `test_reading_the_real_machine_never_raises`
  - `test_the_result_is_cached_between_calls`
  - `test_the_caller_cannot_mutate_the_cache`

</details>

### [test_manager.py](../test/license/test_manager.py) — 54 tests

Tests de la fachada: qué diagnóstico sale de cada archivo, qué habilita cada estado y qué pasa al instalar una licencia nueva.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestReadFeature` | Lo que el config puede escribir en `feature:`. Un pipeline declara UNO. | 4 |
| `TestUnlicensedBuild` |  | 1 |
| `TestDiagnosis` |  | 12 |
| `TestClockRollback` |  | 4 |
| `TestEntitlements` |  | 13 |
| `TestPolicy` |  | 5 |
| `TestExpiryCountdown` |  | 4 |
| `TestModelHashes` |  | 2 |
| `TestInstall` |  | 5 |
| `TestRecheck` |  | 2 |
| `TestStatus` |  | 2 |

<details><summary>Tests</summary>

- `TestReadFeature`
  - `test_a_bare_name_comes_back_as_is`
  - `test_nothing_declared_is_core`
  - `test_whitespace_around_a_name_does_not_make_a_new_feature`
  - `test_a_list_is_a_config_error_and_blocks_the_pipeline`
- `TestUnlicensedBuild`
  - `test_running_from_sources_does_not_enforce_anything`
- `TestDiagnosis`
  - `test_a_valid_license_is_valid`
  - `test_no_file_is_absent`
  - `test_garbage_in_the_file_is_invalid`
  - `test_an_edited_payload_does_not_validate`
  - `test_an_unknown_key_id_is_invalid`
  - `test_a_license_of_another_machine_is_foreign`
  - `test_a_license_without_machine_lock_does_not_run_anywhere`
  - `test_a_single_source_license_does_not_validate_either`
  - `test_a_replaced_disk_still_validates`
  - `test_a_license_of_another_project_is_foreign`
  - `test_an_expired_license_is_expired`
  - `test_a_license_expiring_tomorrow_is_still_valid`
- `TestClockRollback`
  - `test_moving_the_clock_back_makes_the_license_suspect`
  - `test_a_moved_clock_reports_no_days_remaining`
  - `test_the_state_file_path_is_in_the_reason`
  - `test_rollback_is_diagnosed_before_expiry`
- `TestEntitlements`
  - `test_the_camera_quota_cuts_by_declaration_order`
  - `test_the_cut_is_the_same_on_every_run`
  - `test_no_quota_means_every_camera_runs`
  - `test_a_granted_feature_runs`
  - `test_an_ungranted_feature_does_not_run`
  - `test_an_empty_declaration_is_core`
  - `test_anything_that_is_not_a_licensed_name_is_refused`
  - `test_a_pipeline_without_a_declared_feature_is_core`
  - `test_an_expired_license_still_says_what_was_bought`
  - `test_without_a_license_the_camera_feed_still_works`
  - `test_without_a_license_no_pipeline_runs`
  - `test_an_unverifiable_license_no_pipeline_runs_either`
  - `test_running_from_sources_stays_fully_permissive`
- `TestPolicy`
  - `test_warn_keeps_publishing`
  - `test_degrade_stops_publishing`
  - `test_a_valid_license_publishes_whatever_its_policy_says`
  - `test_without_a_license_publishing_is_blocked_regardless_of_the_default_policy`
  - `test_an_unverifiable_license_also_blocks_publishing_unconditionally`
- `TestExpiryCountdown`
  - `test_a_perpetual_license_has_no_countdown`
  - `test_days_remaining_counts_down`
  - `test_an_expired_license_reports_zero_and_not_a_negative`
  - `test_the_warning_window_fires_before_the_line_stops`
- `TestModelHashes`
  - `test_the_licensed_weights_validate`
  - `test_copied_weights_do_not_validate`
- `TestInstall`
  - `test_a_valid_license_gets_installed`
  - `test_a_failed_renewal_leaves_the_previous_license_untouched`
  - `test_a_license_of_another_machine_is_not_installed`
  - `test_a_license_issued_after_the_machine_clock_flags_the_clock`
  - `test_a_file_that_does_not_exist_is_reported_and_not_raised`
- `TestRecheck`
  - `test_nothing_changes_when_nothing_changed`
  - `test_dropping_the_file_in_place_is_picked_up_without_restarting`
- `TestStatus`
  - `test_status_carries_the_documented_keys`
  - `test_status_reports_how_much_of_the_fingerprint_still_matches`

</details>

### [test_policy.py](../test/license/test_policy.py) — 8 tests

Tests de la política: el vocabulario cerrado y qué apaga cada valor.

| Grupo | Tests |
|---|---:|
| `TestNormalize` | 5 |
| `TestWhatEachPolicyDoes` | 3 |

<details><summary>Tests</summary>

- `TestNormalize`
  - `test_the_vocabulary_survives_a_round_trip`
  - `test_case_and_padding_do_not_matter`
  - `test_anything_else_falls_back_to_the_default`
  - `test_the_default_belongs_to_the_vocabulary`
  - `test_every_policy_has_a_description`
- `TestWhatEachPolicyDoes`
  - `test_off_enforces_nothing`
  - `test_warn_applies_entitlements_but_keeps_publishing`
  - `test_degrade_also_stops_the_measurements`

</details>

### [test_public_key.py](../test/license/test_public_key.py) — 9 tests

Tests de la tabla de claves: cuál gana, qué se descarta y qué pasa sin módulo generado.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestWithoutGeneratedModule` | Un checkout de fuentes: no hay build y la tabla en código es todo lo que hay. | 5 |
| `TestWithGeneratedModule` | El build entregado: la tabla la escribió el repositorio de firma. | 4 |

<details><summary>Tests</summary>

- `TestWithoutGeneratedModule`
  - `test_the_template_carries_no_key`
  - `test_a_key_put_in_by_hand_is_found`
  - `test_an_unknown_key_id_is_absent`
  - `test_a_key_of_the_wrong_length_is_treated_as_absent`
  - `test_something_that_is_not_base64url_is_treated_as_absent`
- `TestWithGeneratedModule`
  - `test_the_generated_key_is_used`
  - `test_a_generated_key_of_the_wrong_length_is_absent`
  - `test_the_source_table_still_works_next_to_it`
  - `test_an_empty_generated_table_is_a_build_without_keys`

</details>

### [test_request.py](../test/license/test_request.py) — 6 tests

Tests de la solicitud: qué lleva y cuándo el equipo directamente no se puede licenciar.

| Grupo | Tests |
|---|---:|
| `TestBuildRequest` | 4 |
| `TestSaveRequest` | 2 |

<details><summary>Tests</summary>

- `TestBuildRequest`
  - `test_a_normal_machine_asks_for_the_floor`
  - `test_a_machine_below_the_floor_cannot_be_licensed`
  - `test_a_machine_with_no_sources_at_all_says_so`
  - `test_the_reason_names_the_sources_that_were_read`
- `TestSaveRequest`
  - `test_the_file_is_written_and_is_readable_json`
  - `test_nothing_is_written_when_the_machine_cannot_be_licensed`

</details>

### [test_schema.py](../test/license/test_schema.py) — 26 tests

Tests del formato: qué es un token bien armado, qué campos son obligatorios y cómo se interpretan las fechas.

| Grupo | Tests |
|---|---:|
| `TestBase64Url` | 3 |
| `TestToken` | 3 |
| `TestCanonicalJson` | 2 |
| `TestParsePayload` | 14 |
| `TestDates` | 4 |

<details><summary>Tests</summary>

- `TestBase64Url`
  - `test_round_trip`
  - `test_encoding_has_no_padding_and_survives_a_url`
  - `test_garbage_is_a_format_error`
- `TestToken`
  - `test_round_trip`
  - `test_malformed_tokens_are_rejected`
  - `test_key_id_can_be_read_without_verifying`
- `TestCanonicalJson`
  - `test_key_order_does_not_change_the_bytes`
  - `test_output_is_ascii_and_compact`
- `TestParsePayload`
  - `test_a_complete_license_parses`
  - `test_an_unknown_schema_version_is_rejected`
  - `test_required_fields_are_required`
  - `test_payload_that_is_not_json_is_rejected`
  - `test_payload_that_is_not_an_object_is_rejected`
  - `test_a_quorum_larger_than_the_component_count_is_rejected`
  - `test_a_license_without_a_fingerprint_does_not_even_parse`
  - `test_a_single_source_is_below_the_floor`
  - `test_a_quorum_below_the_floor_is_rejected_even_with_components`
  - `test_the_floor_itself_is_accepted`
  - `test_a_boolean_is_not_a_camera_quota`
  - `test_no_camera_quota_means_no_limit`
  - `test_features_have_to_be_names`
  - `test_an_unknown_policy_falls_back_to_the_default`
- `TestDates`
  - `test_zulu_suffix_is_accepted`
  - `test_a_bare_date_expires_at_the_end_of_that_day`
  - `test_null_expiry_is_perpetual`
  - `test_a_date_that_is_not_a_date_is_rejected`

</details>

### [test_verify.py](../test/license/test_verify.py) — 3 tests

Tests del autocontrol del verificador: una librería editada no puede aceptar licencias.

| Grupo | Tests |
|---|---:|
| `(sin clase)` | 3 |

<details><summary>Tests</summary>

- `(sin clase)`
  - `test_the_real_library_passes_the_canary`
  - `test_a_legitimate_license_still_verifies`
  - `test_a_tampered_verifier_rejects_every_license`

</details>

## test/modbus/

### [test_registers.py](../test/modbus/test_registers.py) — 23 tests

Tests del mapa de registros que se despacha con el repo.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestMapLoads` |  | 3 |
| `TestAddressSpace` |  | 4 |
| `TestConventions` |  | 7 |
| `TestScaledRangesAreUsable` |  | 1 |
| `TestLookupsUsedByTheApp` |  | 5 |
| `TestAMissingMapDoesNotKillTheImport` | Un mapa que falta deja el motivo y un mapa vacío, y no levanta. | 3 |

<details><summary>Tests</summary>

- `TestMapLoads`
  - `test_the_shipped_map_exists`
  - `test_the_map_is_not_empty`
  - `test_schema_and_registers_agree`
- `TestAddressSpace`
  - `test_every_address_fits_in_the_default_datastore`
  - `test_addresses_are_base_1`
  - `test_addresses_are_unique`
  - `test_names_are_unique`
- `TestConventions`
  - `test_every_producer_is_in_the_vocabulary`
  - `test_names_are_snake_case_and_english`
  - `test_every_row_has_a_human_description`
  - `test_scales_are_positive_integers`
  - `test_scaled_rows_declare_their_unit`
  - `test_booleans_are_not_scaled`
  - `test_the_map_declares_no_config_block`
- `TestScaledRangesAreUsable`
  - `test_every_scaled_row_can_carry_a_plausible_value`
- `TestLookupsUsedByTheApp`
  - `test_status_words_are_present`
  - `test_per_camera_block_keeps_a_fixed_stride`
  - `test_the_license_closes_the_exposed_block`
  - `test_the_clock_words_are_consecutive_with_the_high_one_first`
  - `test_readback_addrs_are_a_subset_of_the_map`
- `TestAMissingMapDoesNotKillTheImport`
  - `test_a_map_that_is_not_there_leaves_the_reason`
  - `test_a_broken_map_leaves_the_reason`
  - `test_a_map_that_loads_leaves_no_reason`

</details>

### [test_schema.py](../test/modbus/test_schema.py) — 58 tests

Tests de la maquinaria genérica del esquema de registros: carga del YAML, codificación con escala, espejo de configuración y detección de escrituras del PLC.

| Grupo | Tests |
|---|---:|
| `TestToUint16` | 7 |
| `TestRegDefaults` | 3 |
| `TestLoadRegisters` | 12 |
| `TestRegisterSchemaValidation` | 3 |
| `TestIndexes` | 6 |
| `TestEncode` | 9 |
| `TestMirrorBatch` | 6 |
| `TestDetectPlcWrites` | 5 |
| `TestApplyPlcWrites` | 7 |

<details><summary>Tests</summary>

- `TestToUint16`
  - `test_none_is_zero`
  - `test_plain_value_passes_through`
  - `test_scale_multiplies`
  - `test_rounds_to_nearest`
  - `test_saturates_at_the_top`
  - `test_clamps_negatives_to_zero`
  - `test_accepts_numeric_strings`
- `TestRegDefaults`
  - `test_minimal_row_only_needs_the_four_required_fields`
  - `test_is_frozen`
  - `test_producers_vocabulary`
- `TestLoadRegisters`
  - `test_reads_a_valid_map`
  - `test_empty_file_is_a_valid_empty_map`
  - `test_comments_only_file_is_an_empty_map`
  - `test_a_mapping_at_the_root_is_rejected`
  - `test_a_non_mapping_entry_is_rejected`
  - `test_unknown_key_is_rejected`
  - `test_missing_required_field_is_rejected`
  - `test_empty_required_field_is_rejected`
  - `test_unknown_producer_is_rejected`
  - `test_address_zero_is_rejected`
  - `test_error_message_names_the_file_and_the_row`
  - `test_default_map_path_points_into_the_package`
- `TestRegisterSchemaValidation`
  - `test_duplicate_address_is_rejected`
  - `test_duplicate_name_is_rejected`
  - `test_empty_map_is_valid`
- `TestIndexes`
  - `test_addr_by_name`
  - `test_unknown_name_raises`
  - `test_max_addr`
  - `test_descriptions`
  - `test_readback_addrs_are_only_the_inference_rows`
  - `test_config_regs_are_only_the_config_rows`
- `TestEncode`
  - `test_unscaled_value`
  - `test_scaled_value`
  - `test_boolean_ignores_the_scale`
  - `test_boolean_takes_truthiness`
  - `test_none_becomes_zero`
  - `test_saturates_instead_of_wrapping`
  - `test_encode_batch_maps_names_to_addresses`
  - `test_encode_batch_of_nothing_is_empty`
  - `test_encode_batch_rejects_an_unknown_name`
- `TestMirrorBatch`
  - `test_mirrors_only_the_config_rows`
  - `test_uses_the_default_when_the_key_is_missing`
  - `test_applies_the_scale`
  - `test_encodes_booleans`
  - `test_skips_externally_sourced_rows`
  - `test_skips_rows_without_a_config_key`
- `TestDetectPlcWrites`
  - `test_first_cycle_detects_nothing`
  - `test_a_plc_write_is_detected`
  - `test_an_untouched_register_is_not_a_write`
  - `test_a_local_config_change_is_not_a_plc_write`
  - `test_health_rows_are_never_checked`
- `TestApplyPlcWrites`
  - `test_writes_land_in_config`
  - `test_decodes_the_scale`
  - `test_decodes_booleans`
  - `test_the_raw_value_is_echoed_into_the_batch`
  - `test_a_row_with_a_hook_calls_it_with_the_decoded_value`
  - `test_a_missing_hook_is_not_a_crash`
  - `test_nothing_to_apply_needs_no_save`

</details>

### [test_server.py](../test/modbus/test_server.py) — 57 tests

Tests del servidor Modbus: lectura de config, direccionamiento base-1, el contexto de sólo lectura y el ciclo de vida de los dos transportes.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestSlaveId` |  | 7 |
| `TestRegisterCount` |  | 4 |
| `TestRegisterAccess` |  | 10 |
| `TestReadOnlyContext` |  | 8 |
| `TestAtomicBlock` | Un `update_block()` se lee entero de antes o entero de después. | 4 |
| `TestDisabledTransports` |  | 5 |
| `TestStartupFailures` |  | 4 |
| `TestBlockedByAMissingMap` | Sin mapa de registros no se sirve, y se dice por qué. | 4 |
| `TestBecomingActive` |  | 3 |
| `TestLifecycle` |  | 6 |
| `TestSharedDatastore` |  | 2 |

<details><summary>Tests</summary>

- `TestSlaveId`
  - `test_default_when_the_key_is_missing`
  - `test_reads_a_valid_id`
  - `test_accepts_the_range_edges`
  - `test_accepts_a_numeric_string`
  - `test_out_of_range_falls_back`
  - `test_garbage_falls_back`
  - `test_only_its_own_id_is_registered`
- `TestRegisterCount`
  - `test_default_when_the_key_is_missing`
  - `test_reads_a_valid_count`
  - `test_invalid_count_falls_back`
  - `test_the_datastore_matches_the_count`
- `TestRegisterAccess`
  - `test_registers_start_at_zero`
  - `test_addressing_is_base_1`
  - `test_the_last_register_is_reachable`
  - `test_out_of_map_writes_are_dropped`
  - `test_out_of_map_write_is_logged`
  - `test_out_of_map_reads_return_zero`
  - `test_values_are_masked_to_16_bits`
  - `test_update_block_writes_every_item`
  - `test_update_block_skips_what_falls_outside`
  - `test_update_block_of_nothing_is_harmless`
- `TestReadOnlyContext`
  - `test_reading_holding_registers_inside_the_map_is_allowed`
  - `test_reading_a_single_register_is_allowed`
  - `test_the_last_register_validates`
  - `test_a_read_that_runs_past_the_end_is_rejected`
  - `test_a_negative_address_is_rejected`
  - `test_a_zero_count_is_rejected`
  - `test_other_read_functions_are_rejected`
  - `test_writes_are_rejected`
- `TestAtomicBlock`
  - `test_a_plc_read_never_sees_half_a_block`
  - `test_a_plc_read_waits_for_the_write_lock`
  - `test_the_plc_reads_what_the_block_wrote`
  - `test_a_read_past_the_map_still_gets_its_exception`
- `TestDisabledTransports`
  - `test_status_starts_disabled`
  - `test_disabled_tcp_stays_disabled`
  - `test_disabled_rtu_stays_disabled`
  - `test_tcp_is_enabled_by_default`
  - `test_rtu_is_disabled_by_default`
- `TestStartupFailures`
  - `test_a_denied_bind_leaves_tcp_in_error`
  - `test_a_taken_port_leaves_tcp_in_error`
  - `test_a_missing_serial_port_leaves_rtu_in_error`
  - `test_a_failed_transport_does_not_touch_the_other`
- `TestBlockedByAMissingMap`
  - `test_tcp_does_not_serve`
  - `test_rtu_does_not_serve`
  - `test_it_is_an_error_and_not_a_disabled`
  - `test_without_a_reason_nothing_changes`
- `TestBecomingActive`
  - `test_a_transport_that_survives_the_grace_period_turns_active`
  - `test_a_transport_that_fails_never_turns_active`
  - `test_status_is_starting_before_the_grace_period_ends`
- `TestLifecycle`
  - `test_stop_before_run_does_nothing`
  - `test_stop_is_idempotent`
  - `test_run_returns_after_stop`
  - `test_run_reports_disabled_transports_and_returns`
  - `test_shutdown_keeps_the_error_of_a_transport_that_never_came_up`
  - `test_shutdown_clears_an_active_transport`
- `TestSharedDatastore`
  - `test_the_context_serves_the_registered_device`
  - `test_a_query_to_another_unit_id_finds_nothing`

</details>

## test/telemetry/

### [test_persistence.py](../test/telemetry/test_persistence.py) — 31 tests

Tests del hilo de persistencia: armado del punto, cola que no bloquea, ventana de publicación, aislamiento de un backend roto y vaciado al cerrar.

| Grupo | Tests |
|---|---:|
| `TestBackends` | 5 |
| `TestPushData` | 8 |
| `TestBatching` | 5 |
| `TestLifecycle` | 6 |
| `TestBrokenBackend` | 4 |
| `TestDrain` | 3 |

<details><summary>Tests</summary>

- `TestBackends`
  - `test_by_default_the_repo_backends_are_armed`
  - `test_an_explicit_list_replaces_the_default`
  - `test_an_empty_list_is_respected`
  - `test_the_status_of_a_backend_is_published_by_name`
  - `test_an_unknown_service_is_disabled`
- `TestPushData`
  - `test_the_record_carries_measurement_fields_and_tags`
  - `test_tags_are_optional`
  - `test_the_stamp_is_the_time_of_the_push`
  - `test_an_explicit_stamp_replaces_the_time_of_the_push`
  - `test_pushing_never_blocks_when_the_queue_is_full`
  - `test_a_full_queue_is_warned_once`
  - `test_the_recovery_reports_what_was_lost`
  - `test_pushing_from_several_threads_loses_nothing`
- `TestBatching`
  - `test_the_batch_reaches_every_backend`
  - `test_the_points_of_a_window_travel_together`
  - `test_an_empty_window_does_not_write`
  - `test_the_window_lasts_what_the_constant_says`
  - `test_the_window_is_not_stretched_by_the_queue_wait`
- `TestLifecycle`
  - `test_the_backends_are_opened_by_the_thread`
  - `test_the_backends_are_closed_on_shutdown`
  - `test_the_drain_writes_what_the_window_did_not_reach`
  - `test_the_drain_happens_before_the_backends_close`
  - `test_nothing_is_written_when_there_is_nothing_queued`
  - `test_the_interruption_is_answered_quickly`
- `TestBrokenBackend`
  - `test_a_backend_that_fails_on_setup_does_not_stop_the_others`
  - `test_a_backend_that_fails_on_write_does_not_stop_the_others`
  - `test_a_backend_that_fails_on_close_does_not_stop_the_others`
  - `test_the_thread_finishes_even_with_every_backend_broken`
- `TestDrain`
  - `test_the_drain_empties_the_queue`
  - `test_draining_an_empty_queue_returns_nothing`
  - `test_the_drain_does_not_wait`

</details>

## test/telemetry/backends/

### [test_abstract_backend.py](../test/telemetry/backends/test_abstract_backend.py) — 11 tests

Tests del contrato de los backends de telemetría: qué tiene que implementar un destino nuevo y qué garantiza la base a quien lo consume.

| Grupo | Tests |
|---|---:|
| `TestContract` | 6 |
| `TestStatus` | 4 |
| `TestConfigAccess` | 1 |

<details><summary>Tests</summary>

- `TestContract`
  - `test_every_backend_implements_the_abstraction`
  - `test_every_backend_names_its_service`
  - `test_the_service_names_are_unique`
  - `test_the_abstraction_does_not_name_a_service`
  - `test_each_method_of_the_contract_is_mandatory`
  - `test_the_abstraction_itself_cannot_be_instantiated`
- `TestStatus`
  - `test_a_backend_starts_disabled`
  - `test_the_status_is_read_only_from_outside`
  - `test_the_implementation_moves_its_own_status`
  - `test_the_vocabulary_is_the_one_the_ui_expects`
- `TestConfigAccess`
  - `test_a_disabled_backend_does_nothing`

</details>

### [test_influxdb_backend.py](../test/telemetry/backends/test_influxdb_backend.py) — 36 tests

Tests del backend de InfluxDB: validación de la configuración y la credencial, armado de los puntos, tolerancia a un servidor caído y ciclo de vida.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestSetup` |  | 8 |
| `TestWrite` |  | 13 |
| `TestOutage` | Un corte del servidor no pierde los puntos, y no frena al hilo en cada batch. | 10 |
| `TestClose` |  | 4 |
| `TestIdentity` |  | 1 |

<details><summary>Tests</summary>

- `TestSetup`
  - `test_a_healthy_server_leaves_the_backend_connected`
  - `test_the_write_api_is_created_once_in_the_setup`
  - `test_disabled_by_config_does_not_touch_the_library`
  - `test_disabled_when_the_library_is_missing`
  - `test_an_incomplete_config_is_an_error`
  - `test_a_missing_token_is_an_error`
  - `test_a_client_that_cannot_be_built_is_an_error`
  - `test_a_server_that_does_not_answer_keeps_the_client_armed`
- `TestWrite`
  - `test_the_batch_goes_to_the_configured_bucket`
  - `test_tags_travel_as_text`
  - `test_fields_keep_their_type`
  - `test_the_record_timestamp_is_the_one_that_travels`
  - `test_each_record_keeps_its_own_timestamp`
  - `test_a_record_without_time_is_stamped_by_the_server`
  - `test_a_record_without_measurement_or_fields_is_skipped`
  - `test_a_none_field_does_not_drag_the_rest`
  - `test_valid_records_survive_an_invalid_neighbour`
  - `test_an_empty_batch_does_not_reach_the_server`
  - `test_writing_without_setup_is_a_no_op`
  - `test_a_failed_write_is_reported_but_not_raised`
  - `test_the_backend_recovers_after_a_failed_write`
- `TestOutage`
  - `test_the_points_of_a_failed_write_go_out_with_the_next_one`
  - `test_while_the_server_is_down_it_is_not_called_on_every_batch`
  - `test_it_tries_again_once_the_interval_passes`
  - `test_an_outage_is_logged_once`
  - `test_the_recovery_is_logged_with_what_was_written`
  - `test_a_full_buffer_drops_the_oldest_points`
  - `test_a_rejected_batch_is_not_retried`
  - `test_a_429_is_the_server_asking_to_wait`
  - `test_closing_with_points_pending_says_how_many_are_lost`
  - `test_the_client_gets_a_short_timeout`
- `TestClose`
  - `test_close_releases_the_client`
  - `test_close_is_idempotent`
  - `test_close_without_setup_does_not_fail`
  - `test_writing_after_close_is_a_no_op`
- `TestIdentity`
  - `test_the_backend_names_its_service`

</details>

### [test_mqtt_backend.py](../test/telemetry/backends/test_mqtt_backend.py) — 38 tests

Tests del backend MQTT: armado del cliente para las dos APIs de paho, puerto y credenciales, publicación por tópico, estados de conexión y cierre ordenado.

| Grupo | Tests |
|---|---:|
| `TestBuildClient` | 3 |
| `TestSetup` | 9 |
| `TestCredentials` | 3 |
| `TestPublish` | 11 |
| `TestConnectionState` | 6 |
| `TestClose` | 5 |
| `TestIdentity` | 1 |

<details><summary>Tests</summary>

- `TestBuildClient`
  - `test_paho_2_gets_an_explicit_callback_api_version`
  - `test_paho_1_is_built_without_it`
  - `test_the_client_id_is_the_device_id`
- `TestSetup`
  - `test_the_setup_leaves_the_client_connecting`
  - `test_disabled_by_config_does_not_touch_the_library`
  - `test_disabled_when_the_library_is_missing`
  - `test_an_incomplete_config_is_an_error`
  - `test_a_client_that_cannot_start_is_an_error`
  - `test_the_broker_address_and_keepalive_reach_the_client`
  - `test_the_default_port_depends_on_tls`
  - `test_a_configured_port_wins`
  - `test_a_zero_port_falls_back_to_the_default`
- `TestCredentials`
  - `test_no_user_means_no_authentication`
  - `test_the_password_comes_from_the_environment`
  - `test_a_user_without_password_is_warned`
- `TestPublish`
  - `test_nothing_is_published_before_the_session_exists`
  - `test_the_topic_is_the_base_plus_the_measurement`
  - `test_the_topic_base_is_normalized`
  - `test_the_payload_merges_tags_fields_and_time`
  - `test_a_record_without_time_publishes_without_it`
  - `test_the_publication_is_qos_zero`
  - `test_one_message_per_record`
  - `test_a_record_without_measurement_or_payload_is_skipped`
  - `test_an_empty_batch_publishes_nothing`
  - `test_a_rejected_publication_is_warned_once`
  - `test_a_publish_exception_is_reported_but_not_raised`
- `TestConnectionState`
  - `test_an_accepted_connection_is_connected`
  - `test_a_rejected_connection_is_an_error`
  - `test_a_rejected_connection_publishes_nothing`
  - `test_a_drop_goes_back_to_connecting`
  - `test_nothing_is_published_after_a_drop`
  - `test_a_reconnection_publishes_again`
- `TestClose`
  - `test_the_session_is_cut_before_stopping_the_network_thread`
  - `test_close_leaves_the_backend_disabled`
  - `test_close_is_idempotent`
  - `test_close_without_setup_does_not_fail`
  - `test_publishing_after_close_is_a_no_op`
- `TestIdentity`
  - `test_the_backend_names_its_service`

</details>

## test/tools/camera/

### [test_camera_factory.py](../test/tools/camera/test_camera_factory.py) — 9 tests

Tests de la fábrica de cámaras, el NullDriver y el contrato del driver.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestCameraFactory` |  | 4 |
| `TestCameraFactoryConfigErrors` | Un driver inválido NO debe caer a mock: frames sintéticos con estado 'conectada' ocultarían el error de configuración a la UI y al PLC. | 3 |
| `TestStatusContract` | Todos los drivers tienen que devolver las mismas claves: el consumidor no sabe qué driver le tocó. | 2 |

<details><summary>Tests</summary>

- `TestCameraFactory`
  - `test_mock_driver_created_for_mock`
  - `test_driver_name_is_normalized`
  - `test_driver_is_abstract_subclass`
  - `test_registered_drivers_matches_what_the_factory_builds`
- `TestCameraFactoryConfigErrors`
  - `test_invalid_driver_returns_null_driver`
  - `test_null_driver_never_connects_nor_yields_frames`
  - `test_null_driver_status_reports_disconnected_with_reason`
- `TestStatusContract`
  - `test_status_has_the_stable_keys`
  - `test_markers_are_declared_on_the_abstraction`

</details>

### [test_mock_driver.py](../test/tools/camera/test_mock_driver.py) — 15 tests

Tests del MockDriver: es el único driver que corre sin hardware, así que es donde se verifica el contrato que la base impone a todos.

| Grupo | Tests |
|---|---:|
| `TestCaptureEnabled` | 5 |
| `TestMeasuredFps` | 6 |
| `TestRotation` | 2 |
| `TestDustNoise` | 2 |

<details><summary>Tests</summary>

- `TestCaptureEnabled`
  - `test_capture_is_enabled_by_default`
  - `test_config_can_start_with_capture_disabled`
  - `test_disabled_capture_yields_no_frames_while_still_connected`
  - `test_capture_can_be_re_enabled`
  - `test_setting_the_same_value_is_a_no_op`
- `TestMeasuredFps`
  - `test_fps_is_zero_before_two_frames`
  - `test_fps_is_measured_from_delivered_frames`
  - `test_fps_drops_to_zero_when_the_frames_stop`
  - `test_fps_holds_while_the_frames_keep_coming`
  - `test_a_wall_clock_step_does_not_move_the_measurement`
  - `test_disabled_capture_does_not_add_frames_to_the_measurement`
- `TestRotation`
  - `test_rotation_comes_from_the_base_class`
  - `test_unknown_rotation_is_ignored`
- `TestDustNoise`
  - `test_the_pattern_is_reused_between_frames`
  - `test_it_is_refreshed_once_the_window_passed`

</details>

### [test_rtsp_driver.py](../test/tools/camera/test_rtsp_driver.py) — 41 tests

Tests del driver RTSP: la URL que se arma y el loop de captura.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestStreamUrl` |  | 5 |
| `TestCredentials` | No van al config: se versiona y se comparte. Ver ENV_USER / ENV_PASSWORD. | 5 |
| `TestPerCameraCredentials` | `credentials_env` nombra al secreto, no a la cámara: dos cámaras con la misma cuenta apuntan al mismo sufijo, y una que cambia de slot no obliga a renombrar la variable. | 8 |
| `TestWithoutAddress` |  | 2 |
| `TestStatus` |  | 2 |
| `TestGrabLoop` |  | 6 |
| `TestFpsLimit` |  | 3 |
| `TestFrameCredit` | El tope de entrega es un token bucket, y el reloj entra por parámetro: la secuencia se afirma sin esperar de verdad. | 6 |
| `TestCaptureEnabled` |  | 4 |

<details><summary>Tests</summary>

- `TestStreamUrl`
  - `test_a_full_url_is_used_as_is`
  - `test_a_bare_host_gets_the_scheme`
  - `test_a_host_with_port_and_path_gets_the_scheme`
  - `test_surrounding_whitespace_is_dropped`
  - `test_without_address_there_is_no_url`
- `TestCredentials`
  - `test_they_come_from_the_environment`
  - `test_a_user_without_password_is_still_applied`
  - `test_characters_that_break_the_url_are_escaped`
  - `test_the_address_wins_over_the_environment`
  - `test_the_password_never_reaches_the_log`
- `TestPerCameraCredentials`
  - `test_the_camera_uses_the_variables_of_its_suffix`
  - `test_the_suffix_is_uppercased`
  - `test_two_cameras_can_share_one_secret`
  - `test_a_missing_variable_does_not_fall_back_to_the_shared_account`
  - `test_the_missing_variable_is_named_in_the_log`
  - `test_the_password_of_the_suffix_never_reaches_the_log`
  - `test_the_address_wins_over_the_suffix`
  - `test_without_the_key_the_shared_account_applies`
- `TestWithoutAddress`
  - `test_it_never_connects`
  - `test_the_status_carries_the_reason`
- `TestStatus`
  - `test_the_stable_keys_are_there_without_hardware`
  - `test_temperature_stays_at_zero`
- `TestGrabLoop`
  - `test_the_frame_reaches_the_consumer`
  - `test_the_stream_is_opened_with_ffmpeg_and_timeouts`
  - `test_a_stream_that_does_not_open_leaves_the_driver_disconnected`
  - `test_only_the_newest_frames_survive_a_slow_consumer`
  - `test_a_camera_that_goes_quiet_is_reported_as_a_drop`
  - `test_the_stream_reopens_after_the_camera_goes_quiet`
- `TestFpsLimit`
  - `test_without_a_limit_every_frame_is_decoded`
  - `test_the_extra_frame_is_dropped_before_decoding`
  - `test_a_missing_fps_limit_is_not_a_division_by_zero`
- `TestFrameCredit`
  - `test_a_camera_below_the_cap_loses_no_frame`
  - `test_a_cap_equal_to_the_camera_rate_keeps_every_frame`
  - `test_jitter_within_the_credit_costs_no_frame`
  - `test_a_cap_below_the_camera_rate_throttles`
  - `test_without_a_cap_every_frame_passes`
  - `test_a_pause_does_not_leave_a_backlog_to_catch_up`
- `TestCaptureEnabled`
  - `test_config_can_start_with_capture_disabled`
  - `test_a_disabled_camera_does_not_even_open_the_stream`
  - `test_disabling_it_closes_the_session_without_reporting_a_drop`
  - `test_re_enabling_does_not_deliver_a_stale_frame`

</details>

### [test_st_driver.py](../test/tools/camera/test_st_driver.py) — 28 tests

Tests del driver Sentech: la reconexión del thread de captura.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestGrabLoopReconnect` |  | 4 |
| `TestInterrupt` | El cierre no espera a una cámara que no aparece: ni en `connect()` ni entre reintentos. Sin esto el hilo de captura seguía vivo cuando el cierre se rendía, y Qt abortaba el proceso. | 3 |
| `TestDeviceListRefresh` | Buscar por dirección tiene que re-enumerar antes. | 2 |
| `TestWhyItCouldNotOpen` | «No está» y «está y no abre» se arreglan en lugares opuestos, así que se dicen distinto: lo primero es cable, IP o subred; lo segundo es acceso excluyente. | 2 |
| `TestRetrieveBuffer` | Esperar la tolerancia entera de una vez deja al hilo sordo al cierre, y entonces la limpieza de `_do_grabbing` no corre y la cámara queda reservada. | 4 |
| `TestCameraParams` | Lo que el driver **declara** en la cámara, en vez de heredarlo de su memoria. | 4 |
| `TestTheSessionIsAlwaysReleased` | El bug que dejaba la cámara tomada por el propio proceso. | 3 |
| `TestConnectHandshake` |  | 6 |

<details><summary>Tests</summary>

- `TestGrabLoopReconnect`
  - `test_the_first_pass_enumerates_by_address`
  - `test_while_the_camera_answers_it_reuses_the_cached_id`
  - `test_it_reconnects_after_the_camera_comes_back`
  - `test_a_camera_that_never_returns_keeps_looking_by_address`
- `TestInterrupt`
  - `test_a_waiting_connect_returns_at_once`
  - `test_an_interrupted_driver_does_not_connect_again`
  - `test_the_wait_between_retries_wakes_up_too`
- `TestDeviceListRefresh`
  - `test_it_re_enumerates_before_looking_up_by_address`
  - `test_a_transport_without_refresh_is_not_an_error`
- `TestWhyItCouldNotOpen`
  - `test_found_but_not_openable_says_so`
  - `test_not_found_still_says_not_found`
- `TestRetrieveBuffer`
  - `test_a_slice_without_a_frame_is_not_a_failure`
  - `test_it_asks_in_slices_and_not_for_the_whole_tolerance`
  - `test_closing_wins_over_waiting`
  - `test_it_gives_up_when_the_tolerance_runs_out`
- `TestCameraParams`
  - `test_free_run_is_declared_and_not_inherited`
  - `test_the_packet_size_is_left_alone_unless_it_is_configured`
  - `test_the_packet_size_is_written_when_configured`
  - `test_a_zero_means_do_not_touch`
- `TestTheSessionIsAlwaysReleased`
  - `test_a_capture_error_still_releases_it`
  - `test_a_clean_stop_releases_it_too`
  - `test_a_datastream_that_fails_to_open_releases_it`
- `TestConnectHandshake`
  - `test_a_camera_that_confirms_in_time_connects`
  - `test_a_slow_camera_connects_on_the_next_attempt`
  - `test_a_timeout_does_not_kill_the_thread`
  - `test_a_second_connect_does_not_raise_another_thread`
  - `test_disconnecting_while_waiting_does_not_wait_out_the_timeout`
  - `test_without_the_sdk_it_refuses_without_a_thread`

</details>

### [test_video_file_driver.py](../test/tools/camera/test_video_file_driver.py) — 26 tests

Tests del driver de video grabado: la playlist, el loop y el ritmo por reloj.

| Grupo | Tests |
|---|---:|
| `TestPlaylist` | 5 |
| `TestUnplayableAddress` | 5 |
| `TestLoop` | 2 |
| `TestPacing` | 7 |
| `TestCaptureEnabled` | 3 |
| `TestStatus` | 3 |
| `TestRotation` | 1 |

<details><summary>Tests</summary>

- `TestPlaylist`
  - `test_a_single_file_plays`
  - `test_a_folder_plays_its_videos_in_alphabetical_order`
  - `test_files_that_are_not_videos_are_ignored`
  - `test_a_relative_address_is_anchored_to_the_installation`
  - `test_a_video_added_to_the_folder_enters_on_the_next_connect`
- `TestUnplayableAddress`
  - `test_an_address_without_video_does_not_connect`
  - `test_a_folder_without_videos_does_not_connect`
  - `test_a_file_that_does_not_open_is_skipped`
  - `test_a_list_where_nothing_opens_does_not_connect`
  - `test_a_successful_connect_clears_the_error`
- `TestLoop`
  - `test_the_video_starts_again_after_the_last_frame`
  - `test_the_playlist_starts_again_after_the_last_video`
- `TestPacing`
  - `test_frames_come_out_at_the_video_fps`
  - `test_a_late_consumer_gets_the_frame_of_now_and_loses_the_ones_between`
  - `test_a_long_delay_is_recovered_by_seeking`
  - `test_a_delay_past_the_end_moves_on_to_the_next_video`
  - `test_fps_limit_below_the_video_fps_spaces_the_deliveries`
  - `test_a_frame_not_due_within_the_timeout_returns_none`
  - `test_an_fps_the_container_does_not_report_falls_back_to_fps_limit`
- `TestCaptureEnabled`
  - `test_disabled_capture_yields_no_frames_while_still_connected`
  - `test_the_clock_keeps_running_while_capture_is_disabled`
  - `test_a_disconnected_driver_yields_no_frames`
- `TestStatus`
  - `test_status_has_the_stable_keys`
  - `test_recorded_video_is_marked_as_synthetic`
  - `test_the_driver_builds_without_an_address`
- `TestRotation`
  - `test_rotation_comes_from_the_base_class`

</details>

## test/tools/image/

### [test_enhance.py](../test/tools/image/test_enhance.py) — 30 tests

Tests del ajuste de imagen: qué aclara, qué no toca, cuándo no hay nada que ajustar y cómo se reparte por cámara cuando entra al preprocessor.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestGamma` |  | 6 |
| `TestClahe` |  | 6 |
| `TestBuildDisplayAdjust` |  | 8 |
| `TestBuildImageAdjust` | El ajuste de medición por cámara, con la firma del `preprocessor` del motor. | 10 |

<details><summary>Tests</summary>

- `TestGamma`
  - `test_a_gamma_below_one_brightens`
  - `test_a_gamma_above_one_darkens`
  - `test_it_does_not_clip_the_extremes`
  - `test_a_neutral_or_out_of_range_gamma_changes_nothing`
  - `test_the_input_frame_is_never_touched`
  - `test_it_returns_a_new_array_even_with_no_change`
- `TestClahe`
  - `test_it_changes_a_frame_with_dark_and_bright_zones`
  - `test_it_keeps_the_hue`
  - `test_a_clip_of_zero_changes_nothing`
  - `test_the_clip_saturates`
  - `test_it_works_on_a_gray_frame`
  - `test_the_input_frame_is_never_touched`
- `TestBuildDisplayAdjust`
  - `test_nothing_to_adjust_returns_none`
  - `test_an_out_of_range_gamma_alone_returns_none`
  - `test_gamma_alone_builds_an_adjustment`
  - `test_clahe_alone_builds_an_adjustment`
  - `test_the_two_stack`
  - `test_the_adjustment_returns_a_new_array`
  - `test_an_empty_frame_comes_back_untouched`
  - `test_it_names_itself_with_its_parameters`
- `TestBuildImageAdjust`
  - `test_nothing_to_adjust_returns_none`
  - `test_the_brightness_is_the_linear_factor_of_the_equipment_it_reproduces`
  - `test_above_one_brightens_and_saturates`
  - `test_below_one_darkens`
  - `test_an_out_of_range_factor_is_saturated_not_ignored`
  - `test_each_camera_gets_its_own_parameters`
  - `test_a_camera_without_adjustment_passes_the_same_array`
  - `test_the_local_contrast_runs_on_the_brightened_frame`
  - `test_the_camera_frame_is_never_touched`
  - `test_it_names_the_cameras_it_adjusts`

</details>

### [test_undistort.py](../test/tools/image/test_undistort.py) — 13 tests

Tests de la corrección de lente: qué cámara se corrige, qué pasa con una calibración que no sirve, y que los mapas se calculen una sola vez.

| Grupo | Tests |
|---|---:|
| `TestBuildUndistorter` | 13 |

<details><summary>Tests</summary>

- `TestBuildUndistorter`
  - `test_without_calibration_there_is_nothing_to_build`
  - `test_an_unusable_calibration_is_ignored`
  - `test_it_corrects_the_declared_camera`
  - `test_it_keeps_the_frame_size`
  - `test_a_camera_without_calibration_passes_through`
  - `test_each_camera_uses_its_own_calibration`
  - `test_the_input_frame_is_never_touched`
  - `test_an_empty_frame_comes_back_untouched`
  - `test_two_frames_of_the_same_size_reuse_the_maps`
  - `test_a_different_frame_size_rebuilds_them`
  - `test_keep_field_changes_how_much_is_kept`
  - `test_keep_field_saturates`
  - `test_it_names_the_cameras_it_corrects`

</details>

## test/ui/

### [test_camera_panel.py](../test/ui/test_camera_panel.py) — 4 tests

Tests del panel de cámara: lo que cuesta pintar un frame en el hilo de la GUI.

| Grupo | Tests |
|---|---:|
| `TestPainting` | 4 |

<details><summary>Tests</summary>

- `TestPainting`
  - `test_a_hidden_panel_keeps_the_frame_without_painting_it`
  - `test_it_paints_the_kept_frame_when_shown`
  - `test_the_pixmap_fits_the_label_and_keeps_the_aspect`
  - `test_the_frame_is_shrunk_before_it_reaches_qt`

</details>

### [test_collector_tab.py](../test/ui/test_collector_tab.py) — 15 tests

Tests de la pestaña de dataset: qué modos ofrece según haya ciclo de medición, y que el modo y la lista de cámaras que se guardan vayan y vuelvan del config sin deformarse.

| Grupo | Tests |
|---|---:|
| `TestCameras` | 9 |
| `TestMode` | 6 |

<details><summary>Tests</summary>

- `TestCameras`
  - `test_one_checkbox_per_configured_camera`
  - `test_an_empty_list_checks_every_camera_and_saves_empty`
  - `test_a_missing_key_checks_every_camera`
  - `test_a_list_round_trips`
  - `test_unchecking_one_saves_the_others`
  - `test_checking_them_all_saves_empty`
  - `test_the_last_checked_camera_cannot_be_unchecked`
  - `test_a_slot_that_is_not_configured_goes_back_to_the_file`
  - `test_turning_collection_off_does_not_uncheck_them`
- `TestMode`
  - `test_with_a_cycle_both_modes_are_offered`
  - `test_without_a_cycle_only_interval_is_offered`
  - `test_on_demand_without_a_cycle_is_kept_with_a_warning`
  - `test_on_demand_without_a_cycle_goes_back_to_the_file`
  - `test_choosing_interval_hides_the_warning`
  - `test_the_rule_is_re_evaluated_on_load`

</details>

### [test_form.py](../test/ui/test_form.py) — 4 tests

Tests de los constructores del formulario: la tarjeta de una pestaña no depende de lo que tiene adentro.

| Grupo | Tests |
|---|---:|
| `TestCardWidth` | 3 |
| `TestCardPosition` | 1 |

<details><summary>Tests</summary>

- `TestCardWidth`
  - `test_contents_of_different_width_give_the_same_card`
  - `test_a_narrow_window_shrinks_every_card_alike`
  - `test_a_card_that_is_not_centered_takes_the_whole_width`
- `TestCardPosition`
  - `test_the_card_does_not_move_when_the_scroll_bar_appears`

</details>

### [test_inference_tab.py](../test/ui/test_inference_tab.py) — 9 tests

Tests de la pestaña de inferencia: qué secciones edita y que `load()`/`save()` no deformen lo que hay en el config.

| Grupo | Tests |
|---|---:|
| `TestOverlay` | 1 |
| `TestClassNames` | 2 |
| `TestRoundTrip` | 1 |
| `TestSections` | 3 |
| `TestSlotTabs` | 2 |

<details><summary>Tests</summary>

- `TestOverlay`
  - `test_save_does_not_touch_the_overlay`
- `TestClassNames`
  - `test_save_does_not_write_them`
  - `test_save_does_not_add_them_when_the_config_leaves_them_out`
- `TestRoundTrip`
  - `test_the_model_and_the_pipeline_go_back_as_they_were`
- `TestSections`
  - `test_models_and_pipelines_have_a_bar_each_models_first`
  - `test_one_sub_tab_per_slot`
  - `test_every_slot_round_trips_through_its_sub_tab`
- `TestSlotTabs`
  - `test_a_long_bar_scrolls_instead_of_eliding`
  - `test_ten_pipelines_keep_their_full_labels`

</details>

### [test_log_table.py](../test/ui/test_log_table.py) — 6 tests

Tests de la tabla de eventos: el orden, el tope FIFO y el filtro, y que una línea nueva no reconstruya la tabla entera.

| Grupo | Tests |
|---|---:|
| `TestAppend` | 3 |
| `TestFilter` | 3 |

<details><summary>Tests</summary>

- `TestAppend`
  - `test_the_newest_line_is_on_top`
  - `test_a_full_table_drops_the_oldest_line`
  - `test_a_new_line_does_not_rebuild_the_others`
- `TestFilter`
  - `test_a_filtered_out_line_is_kept_but_not_shown`
  - `test_dropping_a_hidden_line_does_not_remove_a_shown_one`
  - `test_dropping_a_shown_line_removes_it`

</details>

### [test_process_tab.py](../test/ui/test_process_tab.py) — 16 tests

Tests de la pestaña de proceso: de dónde salen sus campos y que `load()`/`save()` no deformen lo que hay en el config.

| Grupo | Tests |
|---|---:|
| `TestFields` | 4 |
| `TestLoad` | 5 |
| `TestSave` | 4 |
| `TestBeltRoiRefresh` | 3 |

<details><summary>Tests</summary>

- `TestFields`
  - `test_it_shows_one_threshold_per_class_of_the_pipeline`
  - `test_a_class_written_in_the_config_is_not_offered`
  - `test_one_sub_tab_per_camera`
  - `test_without_cameras_it_still_builds`
- `TestLoad`
  - `test_it_loads_the_belt_rectangle`
  - `test_it_loads_the_threshold_of_each_class`
  - `test_it_loads_the_averaging_window`
  - `test_a_camera_without_a_rectangle_loads_zeros`
  - `test_loading_twice_does_not_accumulate`
- `TestSave`
  - `test_saving_untouched_writes_back_the_same_values`
  - `test_an_edited_rectangle_reaches_the_config`
  - `test_a_class_without_a_threshold_is_written_as_zero`
  - `test_it_does_not_persist_the_file`
- `TestBeltRoiRefresh`
  - `test_it_rereads_only_the_geometry`
  - `test_an_unknown_camera_is_ignored`
  - `test_the_request_carries_where_to_save_it`

</details>

### [test_system_tab.py](../test/ui/test_system_tab.py) — 2 tests

Tests de la pestaña de sistema: lo que identifica al equipo se muestra pero no se edita.

| Grupo | Tests |
|---|---:|
| `TestDeviceId` | 2 |

<details><summary>Tests</summary>

- `TestDeviceId`
  - `test_it_is_shown_but_not_editable`
  - `test_save_does_not_write_it`

</details>

### [test_theme.py](../test/ui/test_theme.py) — 5 tests

Tests de la paleta: que los dos temas definan lo mismo.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestPaletteKeys` | Las dos paletas declaran las mismas claves. | 2 |
| `TestFooterAlert` | El rol del footer sin licencia válida. | 3 |

<details><summary>Tests</summary>

- `TestPaletteKeys`
  - `test_both_themes_declare_the_same_roles`
  - `test_no_role_is_left_empty`
- `TestFooterAlert`
  - `test_the_alert_role_exists_in_both_themes`
  - `test_it_is_not_the_same_colour_as_the_normal_footer`
  - `test_the_flash_colour_stays_distinct_from_the_alert`

</details>

## test/video/

### [test_abstract_video_server.py](../test/video/test_abstract_video_server.py) — 17 tests

Tests del contrato de los servidores de video: qué tiene que implementar un transporte nuevo y qué garantiza la base a quien empuja frames.

| Grupo | Tests |
|---|---:|
| `TestContract` | 3 |
| `TestStatus` | 3 |
| `TestModes` | 2 |
| `TestPush` | 3 |
| `TestClientPredicates` | 2 |
| `TestCameraSlots` | 4 |

<details><summary>Tests</summary>

- `TestContract`
  - `test_every_server_implements_the_abstraction`
  - `test_the_abstraction_cannot_be_instantiated`
  - `test_a_server_without_the_whole_contract_cannot_be_instantiated`
- `TestStatus`
  - `test_a_server_starts_disabled`
  - `test_the_status_vocabulary_has_no_repeated_values`
  - `test_the_lifecycle_moves_the_status`
- `TestModes`
  - `test_there_is_one_stream_per_camera_and_mode`
  - `test_the_modes_are_the_text_that_goes_in_the_path`
- `TestPush`
  - `test_push_raw_carries_the_slot_and_the_raw_mode`
  - `test_push_annotated_carries_the_slot_and_the_annotated_mode`
  - `test_a_none_frame_reaches_the_implementation`
- `TestClientPredicates`
  - `test_the_predicates_ask_for_the_stream_of_that_mode`
  - `test_an_unknown_slot_has_no_clients`
- `TestCameraSlots`
  - `test_the_slots_keep_the_config_order`
  - `test_the_slots_include_disabled_cameras`
  - `test_no_cameras_section_is_not_an_error`
  - `test_an_empty_cameras_section_is_not_an_error`

</details>

### [test_http_server.py](../test/video/test_http_server.py) — 86 tests

Tests del servidor HTTP de video: reducción de frame, gating por cliente conectado, espera por número de secuencia, ciclo de vida y endpoints.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestDownscale` |  | 6 |
| `TestFrameStore` |  | 8 |
| `TestSequence` | La secuencia es lo que permite no reenviar un frame ya enviado. | 5 |
| `TestWaitForChange` |  | 7 |
| `TestSubscribers` |  | 8 |
| `TestIsClientGone` |  | 5 |
| `TestPartHeader` |  | 2 |
| `TestPushGating` | Lo que ahorra el gating: sin nadie mirando un stream crudo no se codifica nada. | 14 |
| `TestSlots` |  | 3 |
| `TestLifecycle` |  | 7 |
| `TestEndpoints` |  | 7 |
| `TestClientDisconnect` | Cortar la conexión tiene que dejar de costar CPU y ancho de banda enseguida. | 5 |
| `TestConnectionLogging` | Una línea por cliente que entra y sale es útil probando y ruido en producción, así que la decide el config. | 4 |
| `TestStreamNames` | La ruta puede llevar un nombre distinto del slot. | 5 |

<details><summary>Tests</summary>

- `TestDownscale`
  - `test_keeps_the_aspect_ratio`
  - `test_width_zero_leaves_the_native_resolution`
  - `test_negative_width_leaves_the_native_resolution`
  - `test_never_upscales`
  - `test_the_native_width_is_not_copied`
  - `test_an_extreme_aspect_ratio_does_not_collapse_the_height`
- `TestFrameStore`
  - `test_store_starts_empty`
  - `test_update_stores_jpeg_bytes`
  - `test_update_encodes_at_the_requested_width`
  - `test_width_zero_encodes_the_native_resolution`
  - `test_downscaling_reduces_the_jpeg_size`
  - `test_lower_quality_yields_fewer_bytes`
  - `test_each_stream_is_independent`
  - `test_clear_discards_the_last_jpeg`
- `TestSequence`
  - `test_each_update_increments_the_sequence`
  - `test_the_sequence_is_per_stream`
  - `test_get_does_not_consume_the_frame`
  - `test_clear_increments_the_sequence`
  - `test_the_consumer_only_sends_what_is_new`
- `TestWaitForChange`
  - `test_returns_at_once_when_a_newer_frame_already_exists`
  - `test_a_frame_pushed_between_get_and_wait_is_not_missed`
  - `test_wakes_up_when_the_next_frame_arrives`
  - `test_another_stream_does_not_leave_a_frame_to_send`
  - `test_timeout_ends_the_wait`
  - `test_close_wakes_every_waiting_client`
  - `test_a_closed_store_does_not_wait`
- `TestSubscribers`
  - `test_no_leases_counts_zero`
  - `test_touch_registers_a_client`
  - `test_touching_twice_does_not_duplicate`
  - `test_the_count_is_per_stream`
  - `test_several_clients_on_the_same_stream`
  - `test_unregistering_an_unknown_client_is_harmless`
  - `test_an_expired_lease_stops_counting`
  - `test_touch_revives_an_expired_lease`
- `TestIsClientGone`
  - `test_a_live_connection_is_not_reported_gone`
  - `test_a_closed_peer_is_detected`
  - `test_unexpected_data_is_discarded_without_cutting`
  - `test_a_close_after_data_is_detected`
  - `test_a_closed_socket_does_not_raise`
- `TestPartHeader`
  - `test_header_declares_the_boundary_and_the_size`
  - `test_header_ends_with_an_empty_line`
- `TestPushGating`
  - `test_nothing_is_encoded_without_clients`
  - `test_a_watched_stream_is_encoded`
  - `test_the_gating_is_per_stream`
  - `test_the_annotated_frame_is_kept_without_clients`
  - `test_an_unwatched_annotated_frame_is_not_encoded_until_asked`
  - `test_a_kept_frame_does_not_replace_a_newer_encoded_one`
  - `test_the_raw_stream_is_still_gated`
  - `test_annotated_is_encoded_when_watched`
  - `test_an_inactive_server_does_not_encode`
  - `test_a_none_frame_is_ignored`
  - `test_an_unknown_slot_is_a_no_op`
  - `test_frame_width_comes_from_config`
  - `test_jpeg_quality_comes_from_config`
  - `test_the_client_predicates_are_per_stream_and_mode`
- `TestSlots`
  - `test_slots_keep_the_config_order`
  - `test_slots_include_disabled_cameras`
  - `test_no_cameras_still_starts`
- `TestLifecycle`
  - `test_disabled_in_config_does_not_listen`
  - `test_start_reports_active`
  - `test_stop_releases_the_port`
  - `test_stop_is_idempotent`
  - `test_stop_without_start_does_nothing`
  - `test_the_server_can_be_restarted`
  - `test_busy_port_reports_error_instead_of_raising`
- `TestEndpoints`
  - `test_the_info_page_links_every_stream`
  - `test_unknown_paths_are_404`
  - `test_the_stream_declares_the_multipart_content_type`
  - `test_connecting_enables_the_encoding`
  - `test_two_clients_get_the_same_frame`
  - `test_each_camera_serves_its_own_frames`
  - `test_stopping_the_server_ends_the_open_streams`
- `TestClientDisconnect`
  - `test_leaving_stops_the_encoding`
  - `test_the_last_annotated_frame_survives_the_last_client`
  - `test_one_client_leaving_does_not_cut_the_other`
  - `test_a_reset_prints_no_traceback`
  - `test_the_server_keeps_serving_after_a_reset`
- `TestConnectionLogging`
  - `test_connections_are_logged_by_default`
  - `test_nothing_is_logged_when_disabled`
  - `test_the_option_is_read_at_each_event`
  - `test_the_lifecycle_lines_are_not_affected`
- `TestStreamNames`
  - `test_the_route_uses_the_public_name`
  - `test_the_urls_use_the_public_name`
  - `test_without_the_key_the_route_is_the_slot`
  - `test_an_empty_name_is_ignored`
  - `test_a_stopped_server_publishes_no_urls`

</details>

### [test_rtsp_server.py](../test/video/test_rtsp_server.py) — 79 tests

Tests del servidor RTSP: armado del pipeline, escalado, resolución de interfaces, almacén de frames, ritmo del stream y ciclo de vida.

| Grupo | Qué prueba | Tests |
|---|---|---:|
| `TestMountPath` |  | 1 |
| `TestLaunchString` |  | 7 |
| `TestProbeCodec` |  | 5 |
| `TestScaledSize` |  | 7 |
| `TestResolveAddresses` |  | 7 |
| `TestStreamUrls` |  | 4 |
| `TestFrameStore` |  | 13 |
| `TestFramePacer` | El ritmo del stream: qué frame sale, cuándo, y con qué marca de tiempo. | 10 |
| `TestLifecycle` | Sin GStreamer el módulo no levanta nada y expone la misma API. | 8 |
| `TestWatchers` | Las altas y bajas de media son lo único que dice quién está mirando qué. | 6 |
| `TestPushGating` | Lo que ahorra el gating: un stream que nadie mira no retiene frames. | 8 |
| `TestSlots` |  | 3 |

<details><summary>Tests</summary>

- `TestMountPath`
  - `test_the_path_carries_the_slot_and_the_mode`
- `TestLaunchString`
  - `test_the_frames_enter_through_a_live_appsrc`
  - `test_the_scale_filter_is_named_and_starts_open`
  - `test_each_codec_picks_its_encoder`
  - `test_the_hardware_encoder_is_fed_nvmm_memory`
  - `test_every_codec_ends_in_pay0`
  - `test_an_unknown_codec_falls_back_and_warns`
  - `test_the_scaling_comes_before_the_encoder`
- `TestProbeCodec`
  - `test_without_gstreamer_the_reason_is_the_import_error`
  - `test_an_unknown_codec_is_a_reason_and_not_the_default`
  - `test_a_missing_element_is_the_reason`
  - `test_it_builds_the_same_pipeline_the_server_builds`
  - `test_the_probe_pipeline_is_released`
- `TestScaledSize`
  - `test_no_target_width_leaves_the_native_resolution`
  - `test_a_negative_target_width_leaves_the_native_resolution`
  - `test_never_upscales`
  - `test_the_native_width_is_not_scaled`
  - `test_keeps_the_aspect_ratio`
  - `test_both_sides_are_even`
  - `test_an_extreme_aspect_ratio_does_not_collapse_the_height`
- `TestResolveAddresses`
  - `test_no_interfaces_listens_on_all`
  - `test_none_listens_on_all`
  - `test_a_configured_interface_resolves_to_its_ipv4`
  - `test_one_address_per_interface_in_the_configured_order`
  - `test_an_unknown_interface_is_skipped_and_warned`
  - `test_an_interface_without_ipv4_is_skipped`
  - `test_two_interfaces_on_the_same_address_bind_once`
- `TestStreamUrls`
  - `test_one_url_per_address_slot_and_mode`
  - `test_listening_on_all_reports_the_real_addresses`
  - `test_a_host_without_addresses_still_reports_the_port`
  - `test_no_cameras_means_no_urls`
- `TestFrameStore`
  - `test_store_starts_empty`
  - `test_update_keeps_the_frame_as_it_came`
  - `test_only_the_last_frame_survives`
  - `test_each_update_increments_the_sequence`
  - `test_each_stream_is_independent`
  - `test_clear_discards_the_last_frame`
  - `test_clear_increments_the_sequence`
  - `test_clear_only_touches_its_own_stream`
  - `test_wakes_up_when_the_next_frame_arrives`
  - `test_a_frame_pushed_between_get_and_wait_is_not_missed`
  - `test_another_stream_does_not_end_the_wait_early`
  - `test_close_wakes_every_waiting_pipeline`
  - `test_a_closed_store_does_not_wait`
- `TestFramePacer`
  - `test_blocks_until_the_first_frame_arrives`
  - `test_closing_the_store_ends_the_wait`
  - `test_a_closed_store_returns_none_at_once`
  - `test_the_duration_matches_the_declared_fps`
  - `test_the_pts_advances_one_duration_per_frame`
  - `test_only_the_newest_frame_is_sent`
  - `test_the_same_frame_is_not_sent_twice`
  - `test_the_last_frame_is_resent_after_the_keepalive`
  - `test_a_faster_camera_does_not_speed_up_the_stream`
  - `test_each_stream_has_its_own_pacer`
- `TestLifecycle`
  - `test_disabled_in_config_is_not_an_error`
  - `test_enabled_without_gstreamer_reports_error`
  - `test_no_reachable_interface_reports_error`
  - `test_the_disabled_check_comes_first`
  - `test_stop_without_start_does_nothing`
  - `test_stop_after_a_failed_start_does_nothing`
  - `test_no_clients_and_no_urls_when_inactive`
  - `test_pushing_without_a_server_is_a_no_op`
- `TestWatchers`
  - `test_nothing_registered_counts_zero`
  - `test_registering_counts_a_watcher`
  - `test_the_count_is_per_stream`
  - `test_the_same_stream_on_two_interfaces_counts_twice`
  - `test_unregistering_the_last_one_leaves_it_empty`
  - `test_unregistering_an_unknown_stream_does_not_go_negative`
- `TestPushGating`
  - `test_nothing_is_stored_without_watchers`
  - `test_a_watched_stream_stores_what_it_receives`
  - `test_the_gating_is_per_stream`
  - `test_the_client_predicates_are_per_stream_and_mode`
  - `test_a_none_frame_is_ignored`
  - `test_an_inactive_server_does_not_store`
  - `test_stopping_leaves_no_one_watching`
  - `test_the_last_watcher_leaving_drops_the_stored_frame`
- `TestSlots`
  - `test_slots_keep_the_config_order`
  - `test_slots_include_disabled_cameras`
  - `test_no_cameras_is_warned_and_does_not_stop_the_start`

</details>
