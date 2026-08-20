from django.core.management import call_command
from django.test import TestCase

from content.models import ContentSource
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE


class CourseRepositoryRegistrationTests(TestCase):
    def test_management_command_registers_disabled_source_by_default(self) -> None:
        call_command(
            "register_course_repository",
            stable_id="llm-zoomcamp-source",
            display_name="LLM Zoomcamp repository",
            owner="DataTalksClub",
            repository="llm-zoomcamp",
            secret_reference="secretref:llm-zoomcamp-hook",
            stdout=self.stdout,
        )

        source = ContentSource.objects.get(stable_id="llm-zoomcamp-source")
        self.assertFalse(source.enabled)
        self.assertEqual(source.adapter_type, COURSE_REPOSITORY_ADAPTER_TYPE)
        self.assertEqual(source.repository_owner, "DataTalksClub")

    def setUp(self) -> None:
        from io import StringIO

        self.stdout = StringIO()
