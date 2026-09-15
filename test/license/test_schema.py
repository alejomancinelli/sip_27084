"""Tests del formato: qué es un token bien armado, qué campos son obligatorios y cómo se
interpretan las fechas."""

from datetime import datetime, timezone

import pytest

from system.license import schema

from .conftest import TEST_COMPONENTS, build_payload


def to_token(**overrides) -> bytes:
    return schema.to_canonical_json(build_payload(**overrides))


class TestBase64Url:
    def test_round_trip(self):
        raw = bytes(range(256))
        assert schema.decode_b64url(schema.encode_b64url(raw)) == raw

    def test_encoding_has_no_padding_and_survives_a_url(self):
        encoded = schema.encode_b64url(b"cualquier cosa")
        assert "=" not in encoded and "+" not in encoded and "/" not in encoded

    def test_garbage_is_a_format_error(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.decode_b64url("no es base64 ni de casualidad %%%")


class TestToken:
    def test_round_trip(self):
        token = schema.build_token(b"payload", b"firma")
        assert schema.split_token(token) == (b"payload", b"firma")

    @pytest.mark.parametrize("token", ["", "   ", "sinpunto", "a.b.c", ".firma", "payload."])
    def test_malformed_tokens_are_rejected(self, token):
        with pytest.raises(schema.LicenseFormatError):
            schema.split_token(token)

    def test_key_id_can_be_read_without_verifying(self):
        # Leerlo antes de verificar es lo que permite elegir con qué clave probar.
        assert schema.read_key_id(to_token(key_id="iea-2026-a")) == "iea-2026-a"


class TestCanonicalJson:
    def test_key_order_does_not_change_the_bytes(self):
        assert schema.to_canonical_json({"b": 1, "a": 2}) == \
               schema.to_canonical_json({"a": 2, "b": 1})

    def test_output_is_ascii_and_compact(self):
        raw = schema.to_canonical_json({"client": "Ñandú", "n": 1})
        assert b" " not in raw
        assert raw.decode("ascii")


class TestParsePayload:
    def test_a_complete_license_parses(self):
        parsed = schema.parse_payload(to_token())
        assert parsed.license_id == "TEST-0001"
        assert parsed.max_cameras == 2
        assert parsed.features == ("core",)
        assert parsed.is_perpetual

    def test_an_unknown_schema_version_is_rejected(self):
        # Adivinar el significado de un campo de una versión futura es peor que no arrancar.
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(schema=99))

    @pytest.mark.parametrize("missing", ["license_id", "key_id"])
    def test_required_fields_are_required(self, missing):
        payload = build_payload()
        del payload[missing]
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(schema.to_canonical_json(payload))

    def test_payload_that_is_not_json_is_rejected(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(b"{esto no es json")

    def test_payload_that_is_not_an_object_is_rejected(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(b"[1, 2, 3]")

    def test_a_quorum_larger_than_the_component_count_is_rejected(self):
        # Una licencia así no valida en ninguna máquina: es un error de emisión y conviene
        # que se vea al abrirla, no al no arrancar en la planta.
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(fingerprint={"min_matches": 9}))

    def test_a_license_without_a_fingerprint_does_not_even_parse(self):
        # El otro extremo del mismo error, y el que importa: sin componentes la licencia
        # vale en todas las máquinas en vez de en ninguna.
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(fingerprint={"components": {}, "min_matches": 0}))

    def test_a_single_source_is_below_the_floor(self):
        # Una sola fuente es una sola pieza que reemplazar para que la licencia viaje.
        one_source = dict(list(TEST_COMPONENTS.items())[:1])
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(
                to_token(fingerprint={"components": one_source, "min_matches": 1}))

    def test_a_quorum_below_the_floor_is_rejected_even_with_components(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(fingerprint={"min_matches": 1}))

    def test_the_floor_itself_is_accepted(self):
        two_sources = dict(list(TEST_COMPONENTS.items())[:schema.FINGERPRINT_MIN_FLOOR])
        parsed = schema.parse_payload(to_token(
            fingerprint={"components": two_sources,
                         "min_matches": schema.FINGERPRINT_MIN_FLOOR}))
        assert parsed.min_matches == schema.FINGERPRINT_MIN_FLOOR

    def test_a_boolean_is_not_a_camera_quota(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(entitlements={"max_cameras": True}))

    def test_no_camera_quota_means_no_limit(self):
        parsed = schema.parse_payload(to_token(entitlements={"max_cameras": None}))
        assert parsed.max_cameras is None

    def test_features_have_to_be_names(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(entitlements={"features": [1, 2]}))

    def test_an_unknown_policy_falls_back_to_the_default(self):
        parsed = schema.parse_payload(to_token(policy="inventada"))
        assert parsed.policy in ("off", "warn", "degrade")


class TestDates:
    def test_zulu_suffix_is_accepted(self):
        parsed = schema.parse_payload(to_token(expires_at="2027-03-31T12:00:00Z"))
        assert parsed.expires_at == datetime(2027, 3, 31, 12, 0, tzinfo=timezone.utc)

    def test_a_bare_date_expires_at_the_end_of_that_day(self):
        # Una licencia anual se vende por día: vencer a la medianoche del día anterior
        # sorprende al cliente y al que la emitió.
        parsed = schema.parse_payload(to_token(expires_at="2027-03-31"))
        assert parsed.expires_at.date().isoformat() == "2027-03-31"
        assert parsed.expires_at.hour == 23

    def test_null_expiry_is_perpetual(self):
        assert schema.parse_payload(to_token(expires_at=None)).is_perpetual

    def test_a_date_that_is_not_a_date_is_rejected(self):
        with pytest.raises(schema.LicenseFormatError):
            schema.parse_payload(to_token(expires_at="el año que viene"))
