"""Tests de la fachada: qué diagnóstico sale de cada archivo, qué habilita cada estado y
qué pasa al instalar una licencia nueva."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from system.license import fingerprint, manager, policy
from system.license.manager import read_feature

from .conftest import TEST_COMPONENTS, FakeConfig, corrupt_payload, days_from_now

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def compiled_build(monkeypatch):
    """Simula el binario: sin esto, todo cae en `unlicensed_build` y no se enforcea nada."""
    monkeypatch.setattr(manager, "IS_COMPILED", True)
    monkeypatch.setattr(fingerprint, "read_components", lambda **kwargs: dict(TEST_COMPONENTS))


@pytest.fixture
def build_manager(tmp_path, compiled_build):
    """Devuelve `build(token=None, config=None, now=NOW) -> LicenseManager`."""
    license_path = tmp_path / "license.lic"
    state_path = tmp_path / "data" / "license_state.bin"

    def build(token: str | None = None, config=None, now: datetime = NOW):
        if token is not None:
            license_path.write_text(token, encoding="ascii")
        return manager.LicenseManager(
            config or FakeConfig(),
            license_path=str(license_path),
            state_path=str(state_path),
            now_provider=lambda: now,
        )

    return build


class TestReadFeature:
    """Lo que el config puede escribir en `feature:`. Un pipeline declara UNO."""

    def test_a_bare_name_comes_back_as_is(self):
        assert read_feature("security_detection") == "security_detection"

    @pytest.mark.parametrize("declared", [None, "", "   "])
    def test_nothing_declared_is_core(self, declared):
        assert read_feature(declared) == manager.DEFAULT_FEATURE

    def test_whitespace_around_a_name_does_not_make_a_new_feature(self):
        assert read_feature("  core \n") == "core"

    def test_a_list_is_a_config_error_and_blocks_the_pipeline(self, caplog):
        # Dos propósitos independientes son dos pipelines. Una lista acá es un error, y
        # lo que importa es que se avise: bloqueado y en silencio sería lo peor de los
        # dos mundos.
        assert read_feature(["core", "security_detection"]) not in ("core", "")
        assert "feature" in caplog.text.lower()


class TestUnlicensedBuild:
    def test_running_from_sources_does_not_enforce_anything(self, monkeypatch, tmp_path):
        # Sin compilar, sacar la validación es borrar un `if`: fingir que protege algo
        # sería peor que decir que no protege nada.
        monkeypatch.setattr(manager, "IS_COMPILED", False)
        monkeypatch.setattr(fingerprint, "read_components", lambda **kwargs: {})

        license_manager = manager.LicenseManager(
            FakeConfig(), license_path=str(tmp_path / "license.lic"),
            state_path=str(tmp_path / "estado.bin"))

        assert license_manager.state == manager.STATE_UNLICENSED_BUILD
        assert license_manager.policy == policy.POLICY_OFF
        assert license_manager.is_valid
        assert license_manager.is_publishing_allowed()
        assert not license_manager.should_report_invalid()


class TestDiagnosis:
    def test_a_valid_license_is_valid(self, build_manager, sign_license):
        license_manager = build_manager(sign_license())
        assert license_manager.state == manager.STATE_VALID
        assert license_manager.reason == ""
        assert license_manager.license_id == "TEST-0001"

    def test_no_file_is_absent(self, build_manager):
        assert build_manager().state == manager.STATE_ABSENT

    def test_garbage_in_the_file_is_invalid(self, build_manager):
        assert build_manager("no soy una licencia").state == manager.STATE_INVALID

    def test_an_edited_payload_does_not_validate(self, build_manager, sign_license):
        # Subirse el cupo de cámaras a mano rompe la firma, que es todo el punto.
        tampered = corrupt_payload(sign_license(), entitlements={"max_cameras": 99})
        assert build_manager(tampered).state == manager.STATE_INVALID

    def test_an_unknown_key_id_is_invalid(self, build_manager, sign_license):
        forged = corrupt_payload(sign_license(), key_id="clave-que-no-existe")
        license_manager = build_manager(forged)
        assert license_manager.state == manager.STATE_INVALID
        assert "clave" in license_manager.reason.lower()

    def test_a_license_of_another_machine_is_foreign(self, build_manager, sign_license,
                                                    monkeypatch):
        monkeypatch.setattr(fingerprint, "read_components",
                            lambda **kwargs: {"board_uuid": fingerprint.hash_value(
                                "board_uuid", "OTRA-MAQUINA")})
        assert build_manager(sign_license()).state == manager.STATE_FOREIGN

    def test_a_license_without_machine_lock_does_not_run_anywhere(self, build_manager,
                                                                  sign_license, monkeypatch):
        # La forja universal: una licencia sin huella validaba en cualquier equipo, así que
        # una sola copia filtrada corría en toda la industria. El firmante ya no la emite;
        # esto prueba que aunque alguien la emitiera, este equipo no la toma.
        monkeypatch.setattr(fingerprint, "read_components",
                            lambda **kwargs: {"board_uuid": fingerprint.hash_value(
                                "board_uuid", "OTRA-MAQUINA")})
        token = sign_license(fingerprint={"components": {}, "min_matches": 0})
        assert build_manager(token).state == manager.STATE_INVALID

    def test_a_single_source_license_does_not_validate_either(self, build_manager,
                                                              sign_license):
        one_source = dict(list(TEST_COMPONENTS.items())[:1])
        token = sign_license(fingerprint={"components": one_source, "min_matches": 1})
        assert build_manager(token).state == manager.STATE_INVALID

    def test_a_replaced_disk_still_validates(self, build_manager, sign_license, monkeypatch):
        # El caso que de verdad pasa: una pieza cambiada en garantía no puede dejar
        # afuera al cliente que pagó.
        degraded = dict(TEST_COMPONENTS)
        degraded[fingerprint.SOURCE_DISK_SERIAL] = fingerprint.hash_value(
            "disk_serial", "DISCO-NUEVO")
        monkeypatch.setattr(fingerprint, "read_components", lambda **kwargs: degraded)
        assert build_manager(sign_license()).state == manager.STATE_VALID

    def test_a_license_of_another_project_is_foreign(self, build_manager, sign_license):
        other_plant = FakeConfig({"project.client": "ACME", "project.project_id": "LINEA_9"})
        license_manager = build_manager(sign_license(), config=other_plant)
        assert license_manager.state == manager.STATE_FOREIGN
        assert "LINEA_9" in license_manager.reason

    def test_an_expired_license_is_expired(self, build_manager, sign_license):
        token = sign_license(expires_at="2026-01-01T00:00:00Z")
        assert build_manager(token).state == manager.STATE_EXPIRED

    def test_a_license_expiring_tomorrow_is_still_valid(self, build_manager, sign_license):
        token = sign_license(expires_at=(NOW + timedelta(days=1)).isoformat())
        assert build_manager(token).state == manager.STATE_VALID


class TestClockRollback:
    def test_moving_the_clock_back_makes_the_license_suspect(self, build_manager, sign_license):
        token = sign_license()
        assert build_manager(token).state == manager.STATE_VALID

        rewound = build_manager(token, now=NOW - timedelta(days=400))
        assert rewound.state == manager.STATE_TAMPERED

    def test_a_moved_clock_reports_no_days_remaining(self, build_manager, sign_license):
        # La cuenta saldría de una fecha que el propio subsistema acaba de declarar no
        # confiable. Publicar «faltan 400 días» porque alguien movió el reloj es peor que
        # no publicar nada.
        token = sign_license(expires_at=(NOW + timedelta(days=30)).isoformat())
        build_manager(token)

        rewound = build_manager(token, now=NOW - timedelta(days=400))
        assert rewound.state == manager.STATE_TAMPERED
        assert rewound.days_remaining is None
        assert not rewound.is_expiring_soon
        assert rewound.get_status()["days_remaining"] is None

    def test_the_state_file_path_is_in_the_reason(self, build_manager, sign_license, tmp_path):
        # Borrar ese archivo es toda la recuperación que hay, y no existe comando para
        # hacerlo: en un binario compilado no hay CLI. Quien atiende el teléfono no tiene
        # por qué saber la ruta de memoria.
        token = sign_license()
        build_manager(token)
        rewound = build_manager(token, now=NOW - timedelta(days=400))
        assert "license_state.bin" in rewound.reason

    def test_rollback_is_diagnosed_before_expiry(self, build_manager, sign_license):
        # Con la fecha movida, preguntar si venció no significa nada: el diagnóstico útil
        # es el reloj, no el vencimiento.
        token = sign_license(expires_at=(NOW + timedelta(days=10)).isoformat())
        build_manager(token)
        rewound = build_manager(token, now=NOW - timedelta(days=400))
        assert rewound.state == manager.STATE_TAMPERED


class TestEntitlements:
    def test_the_camera_quota_cuts_by_declaration_order(self, build_manager, sign_license):
        license_manager = build_manager(sign_license(entitlements={"max_cameras": 2}))
        slots = ("camera_1", "camera_2", "camera_3", "camera_4")
        assert license_manager.allowed_camera_slots(slots) == ("camera_1", "camera_2")

    def test_the_cut_is_the_same_on_every_run(self, build_manager, sign_license):
        # Que la cámara que se apaga cambie entre arranques sería peor que el límite mismo.
        license_manager = build_manager(sign_license(entitlements={"max_cameras": 1}))
        slots = ("camera_1", "camera_2", "camera_3")
        assert license_manager.allowed_camera_slots(slots) == \
               license_manager.allowed_camera_slots(slots)

    def test_no_quota_means_every_camera_runs(self, build_manager, sign_license):
        license_manager = build_manager(sign_license(entitlements={"max_cameras": None}))
        slots = ("camera_1", "camera_2", "camera_3")
        assert license_manager.allowed_camera_slots(slots) == slots

    def test_a_granted_feature_runs(self, build_manager, sign_license):
        token = sign_license(entitlements={"features": ["core", "security_detection"]})
        assert build_manager(token).has_feature("security_detection")

    def test_an_ungranted_feature_does_not_run(self, build_manager, sign_license):
        assert not build_manager(sign_license()).has_feature("security_detection")

    def test_an_empty_declaration_is_core(self, build_manager, sign_license):
        license_manager = build_manager(sign_license(entitlements={"features": ["core"]}))
        assert license_manager.has_feature("")
        assert license_manager.has_feature(None)

    def test_anything_that_is_not_a_licensed_name_is_refused(self, build_manager,
                                                             sign_license):
        # Ante la duda no se habilita: una forma que el config no debería tener no puede
        # terminar habilitando un addon.
        license_manager = build_manager(sign_license(entitlements={"features": ["core"]}))
        assert not license_manager.has_feature("['core']")

    def test_a_pipeline_without_a_declared_feature_is_core(self, build_manager, sign_license):
        assert build_manager(sign_license(entitlements={"features": ["core"]})).has_feature(None)

    def test_an_expired_license_still_says_what_was_bought(self, build_manager, sign_license):
        # La licencia parsea: se sabe qué se vendió, así que el cupo sigue aplicando.
        token = sign_license(expires_at="2026-01-01", entitlements={"max_cameras": 1})
        license_manager = build_manager(token)
        assert license_manager.state == manager.STATE_EXPIRED
        assert license_manager.allowed_camera_slots(("camera_1", "camera_2")) == ("camera_1",)

    def test_without_a_license_the_camera_feed_still_works(self, build_manager):
        # El equipo abre en modo de puesta en marcha y no se niega a arrancar: el feed en
        # vivo es lo que permite verificar el cableado sin licencia, así que las cámaras
        # no se restringen por esto.
        license_manager = build_manager()
        assert license_manager.allowed_camera_slots(("camera_1", "camera_2")) ==                ("camera_1", "camera_2")

    def test_without_a_license_no_pipeline_runs(self, build_manager):
        # Sin nada que diga qué se compró, lo más restrictivo es lo que corresponde: antes
        # esto frenaba el arranque entero, ahora bloquea todos los pipelines en vez de
        # abortar el proceso.
        license_manager = build_manager()
        assert not license_manager.has_feature("lo_que_sea")
        assert not license_manager.has_feature(None)

    def test_an_unverifiable_license_no_pipeline_runs_either(self, build_manager):
        # Un .lic que no verifica no es mejor que ninguno: no se sabe qué se compró.
        license_manager = build_manager("no soy una licencia")
        assert license_manager.state == manager.STATE_INVALID
        assert not license_manager.has_feature("lo_que_sea")

    def test_running_from_sources_stays_fully_permissive(self, monkeypatch, tmp_path):
        # unlicensed_build no es NO_LICENSE_STATES: sin esto no habría forma de correr el
        # repo ni de probar un pipeline nuevo sin una licencia de prueba a mano.
        monkeypatch.setattr(manager, "IS_COMPILED", False)
        monkeypatch.setattr(fingerprint, "read_components", lambda **kwargs: {})

        license_manager = manager.LicenseManager(
            FakeConfig(), license_path=str(tmp_path / "license.lic"),
            state_path=str(tmp_path / "estado.bin"))

        assert license_manager.state == manager.STATE_UNLICENSED_BUILD
        assert license_manager.has_feature("lo_que_sea")
        assert license_manager.allowed_camera_slots(("camera_1", "camera_2")) ==                ("camera_1", "camera_2")


class TestPolicy:
    def test_warn_keeps_publishing(self, build_manager, sign_license):
        token = sign_license(policy="warn", expires_at="2026-01-01")
        license_manager = build_manager(token)
        assert license_manager.is_publishing_allowed()
        assert license_manager.should_report_invalid()

    def test_degrade_stops_publishing(self, build_manager, sign_license):
        token = sign_license(policy="degrade", expires_at="2026-01-01")
        license_manager = build_manager(token)
        assert not license_manager.is_publishing_allowed()
        assert license_manager.should_report_invalid()

    def test_a_valid_license_publishes_whatever_its_policy_says(self, build_manager,
                                                                sign_license):
        assert build_manager(sign_license(policy="degrade")).is_publishing_allowed()

    def test_without_a_license_publishing_is_blocked_regardless_of_the_default_policy(
            self, build_manager):
        # DEFAULT_POLICY es "warn", que normalmente NO bloquea publicación. Sin licencia
        # utilizable esto no puede depender de esa política: no hay payload firmado del
        # que leerla, así que el bloqueo es incondicional.
        license_manager = build_manager()
        assert license_manager.state == manager.STATE_ABSENT
        assert license_manager.policy == policy.DEFAULT_POLICY
        assert not license_manager.is_publishing_allowed()

    def test_an_unverifiable_license_also_blocks_publishing_unconditionally(
            self, build_manager):
        license_manager = build_manager("no soy una licencia")
        assert license_manager.state == manager.STATE_INVALID
        assert not license_manager.is_publishing_allowed()


class TestExpiryCountdown:
    def test_a_perpetual_license_has_no_countdown(self, build_manager, sign_license):
        license_manager = build_manager(sign_license())
        assert license_manager.days_remaining is None
        assert not license_manager.is_expiring_soon

    def test_days_remaining_counts_down(self, build_manager, sign_license):
        token = sign_license(expires_at=(NOW + timedelta(days=45)).isoformat())
        assert build_manager(token).days_remaining == 45

    def test_an_expired_license_reports_zero_and_not_a_negative(self, build_manager,
                                                                sign_license):
        token = sign_license(expires_at=(NOW - timedelta(days=10)).isoformat())
        assert build_manager(token).days_remaining == 0

    def test_the_warning_window_fires_before_the_line_stops(self, build_manager, sign_license):
        token = sign_license(expires_at=(NOW + timedelta(days=5)).isoformat())
        assert build_manager(token).is_expiring_soon


class TestModelHashes:
    def test_the_licensed_weights_validate(self, build_manager, sign_license, tmp_path):
        weights = tmp_path / "modelo.engine"
        weights.write_bytes(b"pesos del modelo")
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()

        config = FakeConfig({
            "project.client": "ACME",
            "project.project_id": "LINEA_3",
            "inference.models.model_1.path": str(weights),
        })
        token = sign_license(entitlements={"model_hashes": {"model_1": digest}})
        assert build_manager(token, config=config).state == manager.STATE_VALID

    def test_copied_weights_do_not_validate(self, build_manager, sign_license, tmp_path):
        weights = tmp_path / "modelo.engine"
        weights.write_bytes(b"otros pesos")

        config = FakeConfig({
            "project.client": "ACME",
            "project.project_id": "LINEA_3",
            "inference.models.model_1.path": str(weights),
        })
        token = sign_license(entitlements={"model_hashes": {"model_1": "0" * 64}})
        license_manager = build_manager(token, config=config)
        assert license_manager.state == manager.STATE_FOREIGN
        assert "model_1" in license_manager.reason


class TestInstall:
    def test_a_valid_license_gets_installed(self, build_manager, sign_license, tmp_path):
        incoming = tmp_path / "recibida.lic"
        incoming.write_text(sign_license(), encoding="ascii")

        license_manager = build_manager()
        assert license_manager.state == manager.STATE_ABSENT

        is_installed, message = license_manager.install(str(incoming))
        assert is_installed
        assert "TEST-0001" in message
        assert license_manager.state == manager.STATE_VALID

    def test_a_failed_renewal_leaves_the_previous_license_untouched(self, build_manager,
                                                                   sign_license, tmp_path):
        # Es la regla del flujo del cliente: intentar renovar no puede dejar al equipo
        # peor que antes de intentarlo.
        license_manager = build_manager(sign_license())
        assert license_manager.state == manager.STATE_VALID

        broken = tmp_path / "rota.lic"
        broken.write_text("esto no es una licencia", encoding="ascii")

        is_installed, message = license_manager.install(str(broken))
        assert not is_installed and message
        assert license_manager.state == manager.STATE_VALID

    def test_a_license_of_another_machine_is_not_installed(self, build_manager, sign_license,
                                                           tmp_path, monkeypatch):
        monkeypatch.setattr(fingerprint, "read_components",
                            lambda **kwargs: {"board_uuid": fingerprint.hash_value(
                                "board_uuid", "OTRA")})
        incoming = tmp_path / "ajena.lic"
        incoming.write_text(sign_license(), encoding="ascii")

        is_installed, message = build_manager().install(str(incoming))
        assert not is_installed
        assert "equipo" in message

    def test_a_license_issued_after_the_machine_clock_flags_the_clock(self, build_manager,
                                                                     sign_license, tmp_path):
        # La emisión es la única fecha firmada que el equipo ve al instalar: con el reloj
        # ya atrasado de antes, el retroceso se nota ahora y no dentro de un año.
        incoming = tmp_path / "recibida.lic"
        incoming.write_text(sign_license(issued_at=days_from_now(30)), encoding="ascii")

        license_manager = build_manager()
        is_installed, _ = license_manager.install(str(incoming))

        assert is_installed
        assert license_manager.state == manager.STATE_TAMPERED

    def test_a_file_that_does_not_exist_is_reported_and_not_raised(self, build_manager):
        is_installed, message = build_manager().install("no/existe.lic")
        assert not is_installed and message


class TestRecheck:
    def test_nothing_changes_when_nothing_changed(self, build_manager, sign_license):
        assert not build_manager(sign_license()).recheck()

    def test_dropping_the_file_in_place_is_picked_up_without_restarting(self, build_manager,
                                                                       sign_license, tmp_path):
        license_manager = build_manager()
        assert license_manager.state == manager.STATE_ABSENT

        (tmp_path / "license.lic").write_text(sign_license(), encoding="ascii")
        assert license_manager.recheck()
        assert license_manager.state == manager.STATE_VALID


class TestStatus:
    def test_status_carries_the_documented_keys(self, build_manager, sign_license):
        status = build_manager(sign_license(expires_at=days_from_now(90))).get_status()
        expected = {
            "state", "policy", "reason", "should_report_invalid", "license_id",
            "client", "project_id", "issued_at", "expires_at", "days_remaining",
            "is_perpetual", "fingerprint_matches", "fingerprint_required",
            "fingerprint_sources", "max_cameras", "features", "is_compiled",
            "uptime_s",
        }
        assert set(status) == expected

    def test_status_reports_how_much_of_the_fingerprint_still_matches(self, build_manager,
                                                                     sign_license):
        status = build_manager(sign_license()).get_status()
        assert status["fingerprint_matches"] == len(TEST_COMPONENTS)
        assert status["fingerprint_required"] == 2
