from __future__ import annotations

import logging
import subprocess
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from django.http import HttpRequest
from django.test import SimpleTestCase
from gunicorn.config import Config  # type: ignore[import-untyped]
from gunicorn.glogging import Logger  # type: ignore[import-untyped]

from compatibility.monitoring import safe_compatibility_event
from content_sync.course_repository_sync import REQUEST_TIMEOUT_SECONDS
from core.source_policy import (
    GUNICORN_ACCESS_LOG_FORMAT,
    GUNICORN_WEB_TIMEOUT_SECONDS,
    SourcePolicyError,
    analytics_runtime_violations,
    parse_gunicorn_timeout,
    validate_development_source,
    validate_gunicorn_entrypoint,
)
from course_management.datamailer.client import DEFAULT_TIMEOUT_SECONDS
from course_management.observability.events import event_properties
from courses.validators.custom_url_validators import URL_VALIDATION_TIMEOUT
from deploy.contracts import ReleaseContractError
from deploy.development_seo_policy import (
    DEVELOPMENT_TERRAFORM_VARS_PATH,
    REQUIRED_TERRAFORM_PATHS,
    validate_terraform_seo_source,
    validate_trusted_repository_identity,
)

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


def tracked_sources() -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    sources: dict[str, str] = {}
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode()
        try:
            sources[name] = (ROOT / name).read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
    return sources


def terraform_fixture() -> dict[str, str]:
    edge = """
resource "aws_cloudfront_cache_policy" "disabled" {
  default_ttl = 0
  max_ttl = 0
  min_ttl = 0
}
resource "aws_cloudfront_origin_request_policy" "all_viewer" {
  cookies_config {
    cookie_behavior = "all"
  }
  headers_config {
    header_behavior = "allViewer"
  }
  query_strings_config {
    query_string_behavior = "all"
  }
}
resource "aws_cloudfront_response_headers_policy" "security" {
  dynamic "custom_headers_config" {
    for_each = var.robots_header_value == null ? [] : [var.robots_header_value]
    content {
      items {
        header = "X-Robots-Tag"
        override = true
        value = custom_headers_config.value
      }
    }
  }
}
resource "aws_cloudfront_distribution" "this" {
  aliases = [var.hostname]
  default_cache_behavior {
    cache_policy_id = aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.all_viewer.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }
}
"""
    return {
        "modules/django-website/edge.tf": edge,
        DEVELOPMENT_TERRAFORM_VARS_PATH: (
            'hostname = "web.dtcdev.click"\nrobots_header_value = "noindex, nofollow"\n'
        ),
        "tests/fixtures/website-production/main.tf": "robots_header_value = null\n",
    }


class ApplicationSourcePolicyTests(SimpleTestCase):
    def test_tracked_source_has_no_retired_host_development_canonical_or_analytics(self) -> None:
        sources = tracked_sources()
        self.assertIn("core/tests/templates/core/tests/seo_fixture.html", sources)
        validate_development_source(sources)

    def test_source_scan_fails_for_each_injected_policy_violation(self) -> None:
        canonical_host = "web." + "dtcdev.click"
        mutations = (
            {
                "templates/injected.html": (
                    '<link rel="canonical" href="https://' + canonical_host + '/">'
                )
            },
            {
                "templates/injected.html": (
                    '<link href="https://' + canonical_host + '/inverse" rel="canonical">'
                )
            },
            {
                "templates/injected.html": (
                    '<link rel = "canonical" href="https://' + canonical_host + '/spaced">'
                )
            },
            {
                "templates/injected.html": (
                    '<link href="https://' + canonical_host + '/unquoted" rel=canonical>'
                )
            },
            {"notes.txt": "retired=https://" + "dev." + "dtcdev.click/path"},
            {"courses/static/injected.js": "https://www.googletagmanager" + ".com/gtm.js"},
            {"templates/injected.html": "container=" + "GTM-" + "ABC12345"},
        )
        for files in mutations:
            with self.subTest(files=files), self.assertRaises(SourcePolicyError):
                validate_development_source(files)

    def test_analytics_runtime_checker_fails_injected_html_request_and_cookie(self) -> None:
        host = "google-analytics" + ".com"
        cases = (
            {"html": '<script src="https://' + host + '/x.js"></script>'},
            {"request_urls": ["https://sub." + host + "/collect"]},
            {"cookie_names": ["_gcl_fixture"]},
        )
        defaults: dict[str, object] = {"html": "", "request_urls": [], "cookie_names": []}
        for mutation in cases:
            with self.subTest(mutation=mutation):
                values = {**defaults, **mutation}
                self.assertTrue(analytics_runtime_violations(**values))  # type: ignore[arg-type]
        self.assertEqual(
            analytics_runtime_violations(
                html="<main>safe</main>", request_urls=[], cookie_names=[]
            ),
            (),
        )

    def test_entrypoint_uses_only_the_exact_query_safe_access_format(self) -> None:
        source = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        validate_gunicorn_entrypoint(source)
        with self.assertRaises(SourcePolicyError):
            validate_gunicorn_entrypoint(source.replace("%(U)s", "%(r)s", 1))
        with self.assertRaises(SourcePolicyError):
            validate_gunicorn_entrypoint(source + "\n--access-logformat '%(r)s'\n")

    def test_entrypoint_timeout_is_explicit_and_covers_reviewed_client_bounds(self) -> None:
        source = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        gunicorn_line = next(
            line
            for line in source.splitlines()
            if line.strip().startswith("exec uv run --no-sync gunicorn")
        )
        gunicorn_command_without_timeout = gunicorn_line.split("--timeout", 1)[0].rstrip()

        timeout = parse_gunicorn_timeout(source)
        self.assertEqual(timeout, GUNICORN_WEB_TIMEOUT_SECONDS)
        self.assertGreaterEqual(timeout, DEFAULT_TIMEOUT_SECONDS + 10)
        self.assertLessEqual(REQUEST_TIMEOUT_SECONDS, timeout)
        self.assertLessEqual(URL_VALIDATION_TIMEOUT, timeout)

        mutations = (
            ("missing", source.replace("--timeout 90", "", 1)),
            ("duplicate", source.replace("--timeout 90", "--timeout 90 --timeout 90", 1)),
            ("malformed", source.replace("--timeout 90", "--timeout=90", 1)),
            (
                "interpolated",
                source.replace("--timeout 90", '--timeout "${WEB_TIMEOUT:-90}"', 1),
            ),
            ("lower", source.replace("--timeout 90", "--timeout 89", 1)),
            ("higher", source.replace("--timeout 90", "--timeout 91", 1)),
            (
                "second_gunicorn_line",
                source + "\n" + gunicorn_command_without_timeout + "\n",
            ),
        )
        for name, mutated_source in mutations:
            with self.subTest(name=name), self.assertRaises(SourcePolicyError):
                validate_gunicorn_entrypoint(mutated_source)

    def test_gunicorn_runtime_output_excludes_query_headers_cookie_referrer_and_ip(self) -> None:
        canary = "gunicorn-query-canary-36"
        config = Config()
        config.set("accesslog", "-")
        config.set("access_log_format", GUNICORN_ACCESS_LOG_FORMAT)
        gunicorn_logger = Logger(config)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        access_logger = logging.Logger("issue36.gunicorn.access")
        access_logger.addHandler(handler)
        access_logger.propagate = False
        gunicorn_logger.access_log = access_logger
        response = SimpleNamespace(
            status="400 Bad Request",
            sent=24,
            headers=[("X-Request-ID", "request-safe"), ("X-Correlation-ID", "correlation-safe")],
        )
        request = SimpleNamespace(
            headers=[
                ("Referer", canary),
                ("Authorization", canary),
                ("Cookie", canary),
            ]
        )
        environment = {
            "REQUEST_METHOD": "GET",
            "RAW_URI": f"/private/preview/?token={canary}",
            "PATH_INFO": "/private/preview/",
            "QUERY_STRING": f"token={canary}",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "REMOTE_ADDR": canary,
            "HTTP_REFERER": canary,
            "HTTP_USER_AGENT": canary,
        }
        gunicorn_logger.access(response, request, environment, timedelta(microseconds=42))
        output = stream.getvalue()
        self.assertNotIn(canary, output)
        self.assertIn('"GET /private/preview/ HTTP/1.1" 400 24 42', output)
        self.assertIn('request_id="request-safe"', output)
        self.assertIn('correlation_id="correlation-safe"', output)

    def test_application_and_compatibility_events_are_query_and_referrer_safe(self) -> None:
        canary = "event-query-canary-36"
        request = HttpRequest()
        request.path = "/private/preview/"
        request.META["QUERY_STRING"] = f"token={canary}"
        properties = event_properties(request=request)
        event = safe_compatibility_event(
            host="testserver",
            path=request.path,
            method="GET",
            status=400,
            referrer=f"https://external.example/?secret={canary}",
        )
        evidence = repr(properties) + repr(event.properties())
        self.assertNotIn(canary, evidence)


class TerraformSourcePolicyTests(SimpleTestCase):
    def test_accepted_source_fixture_proves_edge_and_zero_ttl_contract(self) -> None:
        evidence = validate_terraform_seo_source(terraform_fixture(), commit=COMMIT)
        self.assertEqual(evidence.commit, COMMIT)
        self.assertEqual(evidence.cache_behavior_count, 1)

    def test_each_terraform_source_regression_fails_closed(self) -> None:
        cases = (
            (DEVELOPMENT_TERRAFORM_VARS_PATH, "web.dtcdev.click", "other.invalid"),
            (DEVELOPMENT_TERRAFORM_VARS_PATH, "noindex, nofollow", "index, follow"),
            ("modules/django-website/edge.tf", "override = true", "override = false"),
            (
                "modules/django-website/edge.tf",
                "for_each = var.robots_header_value == null ? [] : [var.robots_header_value]",
                "for_each = []",
            ),
            ("modules/django-website/edge.tf", "min_ttl = 0", "min_ttl = 60"),
            (
                "modules/django-website/edge.tf",
                "response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id",
                "response_headers_policy_id = null",
            ),
            (
                "modules/django-website/edge.tf",
                'query_string_behavior = "all"',
                'query_string_behavior = "none"',
            ),
            (
                "tests/fixtures/website-production/main.tf",
                "robots_header_value = null",
                'robots_header_value = "noindex, nofollow"',
            ),
        )
        for path, original, replacement in cases:
            with self.subTest(path=path, replacement=replacement):
                sources = terraform_fixture()
                sources[path] = sources[path].replace(original, replacement)
                with self.assertRaises(ReleaseContractError):
                    validate_terraform_seo_source(sources, commit=COMMIT)

    def test_duplicate_commented_and_extra_distribution_regressions_fail_closed(self) -> None:
        mutations = []

        duplicate = terraform_fixture()
        duplicate[DEVELOPMENT_TERRAFORM_VARS_PATH] += 'hostname = "evil.invalid"\n'
        mutations.append(duplicate)

        commented = terraform_fixture()
        commented["modules/django-website/edge.tf"] = commented[
            "modules/django-website/edge.tf"
        ].replace(
            "aliases = [var.hostname]",
            '/* aliases = [var.hostname] */\naliases = ["evil.invalid"]',
        )
        mutations.append(commented)

        extra_distribution = terraform_fixture()
        extra_distribution["modules/django-website/edge.tf"] += (
            '\nresource "aws_cloudfront_distribution" "evil" {\n  aliases = ["evil.invalid"]\n}\n'
        )
        mutations.append(extra_distribution)

        for sources in mutations:
            with self.subTest(source=sources["modules/django-website/edge.tf"]):
                with self.assertRaises(ReleaseContractError):
                    validate_terraform_seo_source(sources, commit=COMMIT)

    def test_cache_control_override_and_incomplete_source_set_fail(self) -> None:
        sources = terraform_fixture()
        sources["modules/django-website/edge.tf"] = sources[
            "modules/django-website/edge.tf"
        ].replace(
            'resource "aws_cloudfront_response_headers_policy" "security" {',
            'resource "aws_cloudfront_response_headers_policy" "security" {\ncache_control = true',
        )
        with self.assertRaises(ReleaseContractError):
            validate_terraform_seo_source(sources, commit=COMMIT)
        incomplete = terraform_fixture()
        incomplete.pop(REQUIRED_TERRAFORM_PATHS[0])
        with self.assertRaises(ReleaseContractError):
            validate_terraform_seo_source(incomplete, commit=COMMIT)

    def test_real_source_interface_requires_canonical_remote_and_origin_main(self) -> None:
        for remote in (
            "git@github.com:DataTalksClub/aws-infra.git",
            "https://github.com/DataTalksClub/aws-infra.git",
        ):
            validate_trusted_repository_identity(remote=remote, revision="origin/main")
        for remote, revision in (
            ("https://github.com/attacker/aws-infra.git", "origin/main"),
            ("https://github.com/DataTalksClub/aws-infra.git", "main"),
            ("https://github.com/DataTalksClub/aws-infra", "origin/main"),
        ):
            with self.subTest(remote=remote, revision=revision):
                with self.assertRaises(ReleaseContractError):
                    validate_trusted_repository_identity(remote=remote, revision=revision)
