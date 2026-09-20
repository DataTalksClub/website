"""The site override of the package Studio shell honours E001 under --deploy.

``community_base.studio.E001`` renders the resolved
``community_base/studio/base.html``. Production uses
``CompressedManifestStaticFilesStorage``. Extending ``studio/base.html`` pulls
in hashed font URLs; without a collected ``staticfiles.json`` that raise is
swallowed and the check reports missing ``content`` / ``studio_content``
blocks. The override must therefore expose those blocks without ``{% static %}``.
"""

from __future__ import annotations

from community_base.studio.checks import check_studio_content_block_contract
from django.test import SimpleTestCase, override_settings


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    },
)
class StudioBaseContractTests(SimpleTestCase):
    def test_content_block_contract_survives_missing_static_manifest(self) -> None:
        self.assertEqual(check_studio_content_block_contract(app_configs=None), [])
