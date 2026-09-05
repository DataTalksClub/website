from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from events.identity import IdentityImportReport


class PrepareDeploymentCommandTests(SimpleTestCase):
    @patch("core.management.commands.prepare_deployment.import_identity_manifest")
    @patch("core.management.commands.prepare_deployment.call_command")
    def test_migrates_before_importing_required_code_owned_data(
        self, migrate: object, import_identities: object
    ) -> None:
        import_identities.return_value = IdentityImportReport(3, 4, 3, 0, 4, False, False)

        call_command("prepare_deployment")

        migrate.assert_called_once_with("migrate", interactive=False)
        import_identities.assert_called_once_with()
