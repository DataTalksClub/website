from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase


class PrepareDeploymentCommandTests(SimpleTestCase):
    @patch("core.management.commands.prepare_deployment.call_command")
    def test_migrates_without_importing_code_owned_content(self, migrate: MagicMock) -> None:
        call_command("prepare_deployment")

        migrate.assert_called_once_with("migrate", interactive=False)
