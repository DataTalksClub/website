import json
import logging
from unittest.mock import patch

from community_base.kernel.context import context_scope
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.safe_logging import (
    RequestContextFilter,
    SafeJsonFormatter,
)
from course_management.middleware import ObservabilityExceptionMiddleware
from course_management.observability import record_event
from course_management.observability.cloudwatch import (
    CloudWatchMetricsConfig,
    CloudWatchMetricsEventBackend,
    cloudwatch_metric_payload,
)
from course_management.observability.events import AppEvent


class ObservabilityEventTests(SimpleTestCase):
    @override_settings(
        OBSERVABILITY_ENVIRONMENT="test",
        OBSERVABILITY_EVENT_SCHEMA_VERSION="1",
        VERSION="20260809-143205-aaaaaaa",
        SOURCE_SHA="a" * 40,
        IMAGE_DIGEST="sha256:" + "b" * 64,
    )
    def test_event_properties_use_project_schema(self):
        event = AppEvent(
            name="homework.submitted",
            distinct_id="user:1",
            properties={
                "course_slug": "mlops",
                "event": "custom",
                "name": "unsafe-log-record-name",
                "level": "spoofed-level",
                "timestamp": "spoofed-timestamp",
                "logger": "spoofed-logger",
                "message": "spoofed-message",
            },
        )

        properties = event.normalized_properties()

        self.assertEqual(properties["event"], "homework.submitted")
        self.assertEqual(properties["schema_version"], "1")
        self.assertEqual(properties["environment"], "test")
        self.assertEqual(properties["version"], "20260809-143205-aaaaaaa")
        self.assertEqual(properties["source_sha"], "a" * 40)
        self.assertEqual(properties["image_digest"], "sha256:" + "b" * 64)
        self.assertEqual(properties["course_slug"], "mlops")
        self.assertEqual(properties["app_event"], "custom")
        self.assertEqual(
            properties["app_name"],
            "unsafe-log-record-name",
        )
        self.assertEqual(properties["app_level"], "spoofed-level")
        self.assertEqual(properties["app_timestamp"], "spoofed-timestamp")
        self.assertEqual(properties["app_logger"], "spoofed-logger")
        self.assertEqual(properties["app_message"], "spoofed-message")

    @override_settings(OBSERVABILITY_EVENT_BACKENDS=["log"])
    def test_record_event_logs_without_external_keys(self):
        with self.assertLogs(
            "course_management.observability.events",
            level="INFO",
        ) as logs:
            record_event(
                "registration.submitted",
                distinct_id="user:10",
                properties={"course_slug": "de"},
            )

        self.assertIn("app_event", logs.output[0])

    @override_settings(
        OBSERVABILITY_ENVIRONMENT="test",
        CLOUDWATCH_APP_METRIC_NAMESPACE="CMP/Test",
        VERSION="20260809-143205-aaaaaaa",
        SOURCE_SHA="a" * 40,
        IMAGE_DIGEST="sha256:" + "b" * 64,
    )
    def test_cloudwatch_metric_payload_uses_emf_schema(self):
        event = AppEvent(
            name="project.submitted",
            distinct_id="user:2",
            properties={"project_slug": "capstone"},
        )

        payload = cloudwatch_metric_payload(
            event,
            config=CloudWatchMetricsConfig(namespace="CMP/Test"),
        )

        self.assertEqual(payload["event"], "project.submitted")
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["environment"], "test")
        self.assertEqual(payload["version"], "20260809-143205-aaaaaaa")
        self.assertEqual(payload["source_sha"], "a" * 40)
        self.assertEqual(payload["image_digest"], "sha256:" + "b" * 64)
        self.assertEqual(payload["project_slug"], "capstone")
        self.assertEqual(payload["distinct_id"], "user:2")
        self.assertEqual(payload["AppEventCount"], 1)
        self.assertIsInstance(payload["_aws"]["Timestamp"], int)
        metric = payload["_aws"]["CloudWatchMetrics"][0]
        self.assertEqual(metric["Namespace"], "CMP/Test")
        self.assertEqual(metric["Dimensions"], [["environment", "event"]])
        self.assertEqual(
            metric["Metrics"],
            [{"Name": "AppEventCount", "Unit": "Count"}],
        )

    @override_settings(CLOUDWATCH_APP_METRIC_NAMESPACE="CMP/Test")
    @patch("course_management.observability.cloudwatch.logger")
    def test_cloudwatch_backend_logs_metric_payload(self, logger_mock):
        event = AppEvent(name="registration.submitted", distinct_id="user:3")

        CloudWatchMetricsEventBackend().record(event)

        logger_mock.info.assert_called_once()
        args, kwargs = logger_mock.info.call_args
        self.assertEqual(args[0], "cloudwatch_app_event")
        extra = kwargs["extra"]
        self.assertEqual(extra["event"], "registration.submitted")
        self.assertEqual(extra["AppEventCount"], 1)
        self.assertEqual(
            extra["_aws"]["CloudWatchMetrics"][0]["Namespace"],
            "CMP/Test",
        )


class ObservabilityExceptionMiddlewareTests(SimpleTestCase):
    @patch("course_management.middleware.report_exception")
    def test_process_exception_reports_through_observability(
        self,
        report_exception_mock,
    ):
        request = RequestFactory().get("/broken")
        exception = ValueError("boom")
        middleware = ObservabilityExceptionMiddleware(lambda request: None)

        result = middleware.process_exception(request, exception)

        self.assertIsNone(result)
        report_exception_mock.assert_called_once_with(
            exception,
            request=request,
            properties={"source": "django.request"},
        )

    def test_raising_view_logs_scrubbed_traceback_with_request_id(self):
        request = RequestFactory().get("/broken")

        def failing_view(_request):
            raise ValueError("Bearer token-canary alice@example.com")

        middleware = ObservabilityExceptionMiddleware(failing_view)
        with context_scope(
            request_id="request-view-1",
            correlation_id="correlation-view-1",
        ):
            with self.assertLogs(
                "course_management.observability.events",
                level="ERROR",
            ) as logs:
                try:
                    middleware(request)
                except ValueError as exception:
                    middleware.process_exception(request, exception)

            record = logs.records[0]
            RequestContextFilter().filter(record)
            payload = json.loads(SafeJsonFormatter().format(record))

        self.assertEqual(payload["request_id"], "request-view-1")
        self.assertEqual(payload["correlation_id"], "correlation-view-1")
        self.assertEqual(payload["exception_type"], "ValueError")
        self.assertTrue(payload["traceback"])
        self.assertNotIn("token-canary", json.dumps(payload))
        self.assertNotIn("alice@example.com", json.dumps(payload))

    @patch("course_management.observability.events.logger")
    def test_report_exception_sanitizes_log_record_fields(self, logger_mock):
        from course_management.observability.events import report_exception

        exception = ValueError("Bearer token-canary alice@example.com")
        try:
            raise exception
        except ValueError as caught:
            report_exception(caught, properties={"name": "unsafe"})

        logger_mock.error.assert_called_once()
        args, kwargs = logger_mock.error.call_args
        self.assertEqual(args[0], "observability exception reported type=%s")
        self.assertEqual(args[1], "ValueError")
        self.assertEqual(kwargs["extra"]["app_name"], "unsafe")
        self.assertEqual(kwargs["extra"]["exception_type"], "ValueError")
        self.assertTrue(kwargs["extra"]["traceback"])
        self.assertNotIn("token-canary", str(kwargs["extra"]))
        self.assertNotIn("alice@example.com", str(kwargs["extra"]))


class SafeJsonLoggingTests(SimpleTestCase):
    def test_event_extras_cannot_overwrite_formatter_owned_fields(self):
        record = logging.LogRecord(
            name="django.request",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=None,
        )
        record.level = "spoofed-level"
        record.timestamp = "spoofed-timestamp"
        record.logger = "spoofed-logger"

        payload = json.loads(SafeJsonFormatter().format(record))

        self.assertEqual(payload["level"], "ERROR")
        self.assertEqual(payload["logger"], "django.request")
        self.assertEqual(payload["message"], "request failed")
        self.assertTrue(payload["timestamp"].endswith("+00:00"))
        self.assertEqual(payload["app_level"], "spoofed-level")
        self.assertEqual(payload["app_timestamp"], "spoofed-timestamp")
        self.assertEqual(payload["app_logger"], "spoofed-logger")

    def test_django_exception_records_include_safe_frames_and_request_ids(self):
        try:
            raise ValueError("Bearer token-canary alice@example.com")
        except ValueError as exception:
            exc_info = (type(exception), exception, exception.__traceback__)

        record = logging.LogRecord(
            name="django.request",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Internal Server Error: /broken",
            args=(),
            exc_info=exc_info,
        )
        with context_scope(
            request_id="request-safe",
            correlation_id="correlation-safe",
        ):
            RequestContextFilter().filter(record)

        rendered = SafeJsonFormatter().format(record)
        payload = json.loads(rendered)

        self.assertEqual(payload["request_id"], "request-safe")
        self.assertEqual(payload["correlation_id"], "correlation-safe")
        self.assertEqual(payload["exception_type"], "ValueError")
        self.assertTrue(payload["traceback"])
        self.assertNotIn("token-canary", rendered)
        self.assertNotIn("alice@example.com", rendered)
