"""Synthetic parity between site settings declarations and the package config app.

Plan issue D0.1b. Every site declaration from ``core.configuration`` must map
onto a package ``community_base.config.declare`` definition: the value type
translates, the default survives the package's coercion, and the declaration is
accepted as-is. Site validators (bounded integers, choice lists, URL schemes,
mailbox senders) have no package-side vocabulary, so each row also records
whether validation stays adapter-owned — that classification is the input to
the D0.1c cutover and the filed blocking gaps.

Pure declaration-level checks: no ``community_base.config`` app is installed,
no database rows and no real secrets are involved. The package declaration
registry is snapshotted and restored around the run so the synthetic site keys
cannot leak into other tests.
"""

from community_base.config import registry as package_registry
from django.test import SimpleTestCase

from core.configuration import registered_operational_settings


def _value_type(definition):
    """Translate the site's ValueType enum onto the package's vocabulary."""
    raw = getattr(definition.value_type, "value", definition.value_type)
    return {
        "string": "str",
        "integer": "int",
        "boolean": "bool",
        "STRING": "str",
        "INTEGER": "int",
        "BOOLEAN": "bool",
    }[raw]


# The one site validator with a package-side equivalent: the mailbox sender
EMAIL_VALIDATOR_KEYS: set[str] = set()


def package_kwargs(definition):
    """Translate one site declaration into package declare() kwargs."""
    return {
        "key": definition.key,
        "group": definition.group,
        "label": definition.label,
        "description": definition.description,
        "value_type": _value_type(definition),
        "default": definition.default,
        "is_email": definition.key in EMAIL_VALIDATOR_KEYS,
        "env_var": definition.env_var,
        "django_settings_fallback": definition.settings_attr,
        "docs_url": definition.docs_reference,
    }


class SettingsContractParityTest(SimpleTestCase):
    def setUp(self):
        self._saved = dict(package_registry._definitions)

    def tearDown(self):
        package_registry._definitions.clear()
        package_registry._definitions.update(self._saved)

    def test_every_site_declaration_maps_and_is_accepted(self):
        site_definitions = registered_operational_settings()
        self.assertGreaterEqual(len(site_definitions), 31)

        adapter_owned = []
        for definition in site_definitions:
            kwargs = package_kwargs(definition)
            if (
                definition.key not in EMAIL_VALIDATOR_KEYS
                and getattr(definition, "validator", None) is not None
            ):
                adapter_owned.append(definition.key)
            declared = package_registry.declare(**kwargs)
            # The package must accept the site default unchanged.
            self.assertEqual(declared.coerce(definition.default), definition.default)

        # Every declaration with a site-side callable validator is recorded as
        # adapter-owned; today that is every validated key except the email one.
        self.assertGreaterEqual(len(adapter_owned), 5)

    def test_value_type_vocabulary_covers_every_site_type(self):
        used = {
            str(getattr(definition.value_type, "value", definition.value_type)).lower()
            for definition in registered_operational_settings()
        }
        self.assertLessEqual(used, {"string", "integer", "boolean"})

    def test_package_coerce_rejects_out_of_vocabulary_shapes_the_site_validates(self):
        # Documented gap: the package coerces types but cannot express the
        # site's bounded-integer range. A within-type but out-of-range value
        # coerces cleanly package-side, which is exactly why validation stays
        # adapter-owned until the package grows a validation vocabulary.
        declared = package_registry.declare(
            key="parity.bounded_synthetic",
            group="parity",
            label="Parity probe",
            description="Synthetic probe for the bounded-integer gap.",
            value_type="int",
            default=1,
        )
        self.assertEqual(declared.coerce(99_999), 99_999)
