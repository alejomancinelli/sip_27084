"""Tests de la política: el vocabulario cerrado y qué apaga cada valor."""

import pytest

from system.license import policy


class TestNormalize:
    @pytest.mark.parametrize("value", policy.POLICIES)
    def test_the_vocabulary_survives_a_round_trip(self, value):
        assert policy.normalize(value) == value

    def test_case_and_padding_do_not_matter(self):
        assert policy.normalize("  DEGRADE ") == policy.POLICY_DEGRADE

    @pytest.mark.parametrize("value", [None, "", "inventada", 7, []])
    def test_anything_else_falls_back_to_the_default(self, value):
        assert policy.normalize(value) == policy.DEFAULT_POLICY

    def test_the_default_belongs_to_the_vocabulary(self):
        assert policy.DEFAULT_POLICY in policy.POLICIES

    def test_every_policy_has_a_description(self):
        assert set(policy.DESCRIPTIONS) == set(policy.POLICIES)


class TestWhatEachPolicyDoes:
    def test_off_enforces_nothing(self):
        assert not policy.should_enforce_entitlements(policy.POLICY_OFF)
        assert not policy.should_block_publishing(policy.POLICY_OFF)
        assert not policy.should_report_invalid(policy.POLICY_OFF)

    def test_warn_applies_entitlements_but_keeps_publishing(self):
        # «Warn» es sobre la licencia inválida, no sobre el cupo: una licencia de cuatro
        # cámaras no habilita cinco por más suave que sea la política.
        assert policy.should_enforce_entitlements(policy.POLICY_WARN)
        assert not policy.should_block_publishing(policy.POLICY_WARN)
        assert policy.should_report_invalid(policy.POLICY_WARN)

    def test_degrade_also_stops_the_measurements(self):
        assert policy.should_enforce_entitlements(policy.POLICY_DEGRADE)
        assert policy.should_block_publishing(policy.POLICY_DEGRADE)
        assert policy.should_report_invalid(policy.POLICY_DEGRADE)
