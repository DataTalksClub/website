import json
import os
import subprocess
import sys
from contextvars import copy_context
from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.test import SimpleTestCase

from core.bootstrap import (
    BootstrapConfigurationError,
    RuntimeEnvironment,
    database_configuration,
    parse_bool,
    parse_database_url,
    parse_environment,
    parse_int,
    parse_list,
    parse_secret,
)
from core.context import (
    ContextIdError,
    bind_context,
    context_scope,
    current_context,
    external_context_id_or_new,
    reset_context,
    validate_context_id,
)
from core.middleware import RequestIdMiddleware
from core.redaction import CYCLE, REDACTED, TRUNCATED, RedactionPolicy, redact, redact_value
from core.services import ServiceContext


class BootstrapParserTests(SimpleTestCase):
    def import_deployed_settings(
        self,
        module: str,
        *,
        overrides: dict[str, str] | None = None,
        command: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "DATABASE_URL",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "DJANGO_SECRET_KEY",
            "DTC_ENVIRONMENT",
            "DTC_USE_SQLITE",
            "DATAMAILER_SYNC_ON_USER_CREATE",
            "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY",
            "DATAMAILER_TRANSACTIONAL_DRY_RUN",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "DATABASE_URL": "postgresql://check:check@127.0.0.1:5432/check",
                "DJANGO_ALLOWED_HOSTS": (
                    "web.dtcdev.click" if module == "development" else "example.invalid"
                ),
                "DJANGO_CSRF_TRUSTED_ORIGINS": (
                    "https://web.dtcdev.click"
                    if module == "development"
                    else "https://example.invalid"
                ),
                "DJANGO_SECRET_KEY": "strong-test-only-secret-" + "x" * 48,
                "DTC_ENVIRONMENT": module,
            }
        )
        environment.update(overrides or {})
        return subprocess.run(
            [
                sys.executable,
                "-c",
                command or f"import website.settings.{module}",
            ],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_boolean_integer_and_list_parsers_are_strict_and_typed(self) -> None:
        self.assertIs(parse_bool("FEATURE_ENABLED", "true"), True)
        self.assertIs(parse_bool("FEATURE_ENABLED", "false"), False)
        self.assertIs(parse_bool("FEATURE_ENABLED", "1"), True)
        self.assertIs(parse_bool("FEATURE_ENABLED", "0"), False)
        self.assertIs(parse_bool("FEATURE_ENABLED", None, default=False), False)
        self.assertEqual(parse_int("WORKERS", "4", minimum=1, maximum=8), 4)
        self.assertEqual(
            parse_list("HOSTS", "one.example, two.example"), ("one.example", "two.example")
        )

        for invalid_boolean in (
            "TRUE",
            "FALSE",
            "yes",
            "on",
            " true ",
            "secret-canary",
        ):
            with (
                self.subTest(boolean=invalid_boolean),
                self.assertRaises(BootstrapConfigurationError) as error,
            ):
                parse_bool("FEATURE_ENABLED", invalid_boolean)
            self.assertNotIn(invalid_boolean, str(error.exception))
        for invalid_integer in ("01", "+1", "1.0", " 1", True):
            with (
                self.subTest(integer=invalid_integer),
                self.assertRaises(BootstrapConfigurationError),
            ):
                parse_int("WORKERS", invalid_integer)
        for invalid_list in ("one,,two", "one,one"):
            with self.subTest(items=invalid_list), self.assertRaises(BootstrapConfigurationError):
                parse_list("HOSTS", invalid_list)

    def test_deployed_settings_accept_exact_task_definition_boolean_literals(self) -> None:
        task_booleans = {
            "DATAMAILER_SYNC_ON_USER_CREATE": "0",
            "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY": "0",
            "DATAMAILER_TRANSACTIONAL_DRY_RUN": "1",
        }
        for module in ("development", "production"):
            with self.subTest(module=module):
                result = self.import_deployed_settings(module, overrides=task_booleans)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployed_settings_reject_sqlite_without_echoing_database_url(self) -> None:
        rejected = "sqlite:///sqlite-sensitive-canary.db"
        for module in ("development", "production"):
            with self.subTest(module=module):
                result = self.import_deployed_settings(
                    module,
                    overrides={"DATABASE_URL": rejected},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid bootstrap setting DATABASE_URL", result.stderr)
                self.assertNotIn(rejected, result.stderr)
                self.assertNotIn("sqlite-sensitive-canary", result.stderr)

    def test_development_rejects_every_non_postgresql_database_without_echoing_url(self) -> None:
        rejected = "mysql://user:database-password-canary@db.example.invalid/site"
        result = self.import_deployed_settings(
            "development",
            overrides={"DATABASE_URL": rejected},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid bootstrap setting DATABASE_URL", result.stderr)
        self.assertNotIn(rejected, result.stderr)
        self.assertNotIn("database-password-canary", result.stderr)

    def test_deployed_settings_reject_malformed_database_urls_without_echoing_them(self) -> None:
        rejected_values = (
            "postgresql://user:malformed-port-canary@db.example.invalid:not-a-port/site",
            "unknown-scheme-canary://user:password@db.example.invalid/site",
        )
        for module in ("development", "production"):
            for rejected in rejected_values:
                with self.subTest(module=module, rejected=rejected):
                    result = self.import_deployed_settings(
                        module,
                        overrides={"DATABASE_URL": rejected},
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Invalid bootstrap setting DATABASE_URL", result.stderr)
                    self.assertNotIn(rejected, result.stderr)
                    self.assertNotIn("canary", result.stderr)

    def test_deployed_settings_reject_malformed_boolean_without_echoing_value(self) -> None:
        rejected = "YES-sensitive-boolean-canary"
        for module in ("development", "production"):
            with self.subTest(module=module):
                result = self.import_deployed_settings(
                    module,
                    overrides={"DATAMAILER_TRANSACTIONAL_DRY_RUN": rejected},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "Invalid bootstrap setting DATAMAILER_TRANSACTIONAL_DRY_RUN",
                    result.stderr,
                )
                self.assertNotIn(rejected, result.stderr)
                self.assertNotIn("sensitive-boolean-canary", result.stderr)

    def test_production_import_never_loads_repository_dotenv(self) -> None:
        command = (
            "from unittest.mock import patch; "
            "guard = patch('dotenv.load_dotenv', side_effect=RuntimeError('dotenv-loaded')); "
            "guard.start(); import website.settings.production"
        )
        result = self.import_deployed_settings("production", command=command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("dotenv-loaded", result.stderr)

    def test_deployed_environment_is_explicit_valid_and_matches_settings_module(self) -> None:
        for module, mismatched in (
            ("development", "production"),
            ("production", "development"),
        ):
            with self.subTest(module=module, mismatched=mismatched):
                result = self.import_deployed_settings(
                    module,
                    overrides={"DTC_ENVIRONMENT": mismatched},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid bootstrap setting DTC_ENVIRONMENT", result.stderr)

        rejected = "unknown-environment-canary"
        result = self.import_deployed_settings(
            "production",
            overrides={"DTC_ENVIRONMENT": rejected},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid bootstrap setting DTC_ENVIRONMENT", result.stderr)
        self.assertNotIn(rejected, result.stderr)
        self.assertNotIn("environment-canary", result.stderr)

    def test_deployed_secret_rejects_weak_or_control_values_without_echoing_them(self) -> None:
        rejected_values = (
            "short-secret-canary",
            "long-enough-control-secret-canary-abcdef0123456789\nunsafe",
            "x" * 64,
        )
        for module in ("development", "production"):
            for rejected in rejected_values:
                with self.subTest(module=module, rejected=rejected):
                    result = self.import_deployed_settings(
                        module,
                        overrides={"DJANGO_SECRET_KEY": rejected},
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Invalid bootstrap setting DJANGO_SECRET_KEY", result.stderr)
                    self.assertNotIn(rejected, result.stderr)
                    self.assertNotIn("secret-canary", result.stderr)

        accepted = "strong-test-only-secret-" + "x" * 48
        self.assertEqual(parse_secret("DJANGO_SECRET_KEY", accepted), accepted)

    def test_deployed_secret_rejects_padded_forbidden_values_without_echoing_them(self) -> None:
        rejected_values = (
            "          dtc-local-only-insecure-secret-key          ",
            "          django-insecure-" + "x" * 48 + "          ",
        )
        for module in ("development", "production"):
            for rejected in rejected_values:
                with self.subTest(module=module, rejected=rejected):
                    result = self.import_deployed_settings(
                        module,
                        overrides={"DJANGO_SECRET_KEY": rejected},
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Invalid bootstrap setting DJANGO_SECRET_KEY", result.stderr)
                    self.assertNotIn(rejected.strip(), result.stderr)

    def test_environment_and_database_policy_fail_closed_without_echoing_values(self) -> None:
        self.assertEqual(parse_environment(None), RuntimeEnvironment.LOCAL)
        self.assertEqual(parse_environment("production"), RuntimeEnvironment.PRODUCTION)
        production = parse_database_url(
            "DATABASE_URL",
            "postgresql://user:password@db.example.invalid/site",
            environment=RuntimeEnvironment.PRODUCTION,
        )
        self.assertEqual(production["ENGINE"], "django.db.backends.postgresql")

        sensitive_urls = (
            "sqlite:///production.sqlite3",
            "mysql://user:password-canary@db.example.invalid/site",
            "not-a-database-url-with-password-canary",
        )
        for value in sensitive_urls:
            with self.subTest(value=value), self.assertRaises(BootstrapConfigurationError) as error:
                parse_database_url(
                    "DATABASE_URL",
                    value,
                    environment=RuntimeEnvironment.PRODUCTION,
                )
            self.assertNotIn(value, str(error.exception))
            self.assertNotIn("password-canary", str(error.exception))

    def test_sqlite_fallback_is_explicit_and_limited_to_local_or_test(self) -> None:
        path = Path("explicit-test.sqlite3")
        config = database_configuration(
            environment=RuntimeEnvironment.TEST,
            database_url=None,
            sqlite_fallback=path,
        )
        self.assertEqual(config, {"ENGINE": "django.db.backends.sqlite3", "NAME": path})
        with self.assertRaises(BootstrapConfigurationError):
            database_configuration(
                environment=RuntimeEnvironment.PRODUCTION,
                database_url=None,
                sqlite_fallback=path,
            )


class ContextMiddlewareTests(SimpleTestCase):
    def tearDown(self) -> None:
        self.assertEqual(current_context().request_id, None)
        self.assertEqual(current_context().correlation_id, None)
        self.assertEqual(current_context().job_id, None)

    def test_context_scope_nests_and_resets_exactly(self) -> None:
        with context_scope(request_id="outer", correlation_id="correlation-1"):
            self.assertEqual(current_context().request_id, "outer")
            with context_scope(request_id="inner", correlation_id="correlation-2", job_id="job-1"):
                self.assertEqual(
                    current_context(),
                    current_context().__class__("inner", "correlation-2", "job-1"),
                )
            self.assertEqual(current_context().request_id, "outer")
            self.assertEqual(current_context().correlation_id, "correlation-1")

    def test_context_validation_error_does_not_echo_rejected_value(self) -> None:
        rejected = "secret value with spaces"
        with self.assertRaises(ContextIdError) as error:
            validate_context_id("request_id", rejected)
        self.assertNotIn(rejected, str(error.exception))

    def test_copied_contexts_remain_isolated_when_bindings_are_interleaved(self) -> None:
        first = copy_context()
        second = copy_context()
        first_tokens = first.run(
            bind_context,
            request_id="request-first",
            correlation_id="correlation-first",
        )
        second_tokens = second.run(
            bind_context,
            request_id="request-second",
            correlation_id="correlation-second",
        )
        self.assertEqual(first.run(current_context).request_id, "request-first")
        self.assertEqual(second.run(current_context).request_id, "request-second")
        self.assertIsNone(current_context().request_id)
        second.run(reset_context, second_tokens)
        self.assertEqual(first.run(current_context).correlation_id, "correlation-first")
        first.run(reset_context, first_tokens)

    def test_middleware_propagates_valid_ids_and_resets_after_response(self) -> None:
        observed = []

        def response(request: HttpRequest) -> HttpResponse:
            observed.append((request.request_id, request.correlation_id, current_context()))  # type: ignore[attr-defined]
            return HttpResponse("ok")

        request = HttpRequest()
        request.META["HTTP_X_REQUEST_ID"] = "request-1"
        request.META["HTTP_X_CORRELATION_ID"] = "correlation-1"
        result = RequestIdMiddleware(response)(request)
        self.assertEqual(result.headers["X-Request-ID"], "request-1")
        self.assertEqual(result.headers["X-Correlation-ID"], "correlation-1")
        self.assertEqual(observed[0][2].request_id, "request-1")

    def test_middleware_replaces_invalid_headers_and_resets_on_exception(self) -> None:
        def failure(request: HttpRequest) -> HttpResponse:
            self.assertNotEqual(request.request_id, "credential canary")  # type: ignore[attr-defined]
            self.assertEqual(request.request_id, request.correlation_id)  # type: ignore[attr-defined]
            raise RuntimeError("expected")

        request = HttpRequest()
        request.META["HTTP_X_REQUEST_ID"] = "credential canary"
        request.META["HTTP_X_CORRELATION_ID"] = "also invalid"
        with self.assertRaisesRegex(RuntimeError, "expected"):
            RequestIdMiddleware(failure)(request)

    def test_middleware_replaces_credential_shaped_external_ids_without_echoing_them(self) -> None:
        credentials = (
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "AKIAABCDEFGHIJKLMNOP",
            "eyJheader.payload.signature",
        )
        for credential in credentials:
            observed: list[tuple[str, str]] = []

            def response(
                request: HttpRequest,
                *,
                observed_items: list[tuple[str, str]] = observed,
            ) -> HttpResponse:
                observed_items.append((request.request_id, request.correlation_id))  # type: ignore[attr-defined]
                return HttpResponse("ok")

            request = HttpRequest()
            request.META["HTTP_X_REQUEST_ID"] = credential
            request.META["HTTP_X_CORRELATION_ID"] = credential
            result = RequestIdMiddleware(response)(request)

            with self.subTest(credential=credential):
                self.assertNotEqual(observed[0][0], credential)
                self.assertEqual(observed[0][0], observed[0][1])
                self.assertNotIn(credential, tuple(result.headers.values()))
                self.assertEqual(validate_context_id("request_id", credential), credential)
                self.assertNotEqual(external_context_id_or_new(credential), credential)


class RedactionAndServiceConventionTests(SimpleTestCase):
    def test_redaction_normalizes_sensitive_keys_and_protects_canaries(self) -> None:
        canary = "provider-secret-canary"
        original_safe = {"note": f"prefix {canary} suffix", "count": 2}
        original: dict[str, object] = {
            "Authorization-Header": "Bearer token",
            "aws_secret.ACCESS-key": "secret",
            "safe": original_safe,
        }
        redacted = redact(original, canaries=(canary,))
        self.assertEqual(redacted["Authorization-Header"], REDACTED)  # type: ignore[index]
        self.assertEqual(redacted["aws_secret.ACCESS-key"], REDACTED)  # type: ignore[index]
        self.assertEqual(redacted["safe"]["note"], REDACTED)  # type: ignore[index]
        self.assertEqual(original_safe["note"], f"prefix {canary} suffix")
        self.assertNotIn(canary, json.dumps(redacted))

    def test_redaction_blocks_sensitive_aliases_across_naming_conventions(self) -> None:
        aliases = (
            "requestBody",
            "request_body",
            "request-body",
            "request.body",
            "x-api-key",
            "password_id",
            "accessToken",
            "token_hash",
        )
        redacted = redact({alias: "credential-canary" for alias in aliases})
        self.assertEqual(redacted, {alias: REDACTED for alias in aliases})

    def test_redaction_is_bounded_and_cycle_safe(self) -> None:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        value = {
            "cycle": cyclic,
            "items": [1, 2, 3],
            "long": "x" * 10,
            "deep": {"one": {"two": "three"}},
        }
        redacted = redact(
            value,
            policy=RedactionPolicy(
                max_depth=2,
                max_container_items=2,
                max_total_nodes=20,
                max_string_length=4,
            ),
        )
        rendered = repr(redacted)
        self.assertIn(CYCLE, rendered)
        self.assertIn(TRUNCATED, rendered)

    def test_redaction_protects_pii_and_management_links_under_innocent_keys(self) -> None:
        value = {
            "contact": "person@example.com",
            "next": "https://studio.example.invalid/users/42?token=canary",
            "safe": "public label",
        }
        self.assertEqual(
            redact_value(value),
            {"contact": REDACTED, "next": REDACTED, "safe": "public label"},
        )

    def test_excessive_canary_input_fails_closed_without_traversing_the_value(self) -> None:
        value = {"safe": "would otherwise remain"}
        canaries = tuple(f"canary-{index}" for index in range(65))
        self.assertEqual(redact(value, canaries=canaries), REDACTED)

    def test_service_context_captures_current_ids_without_global_state(self) -> None:
        with context_scope(request_id="request-1", correlation_id="correlation-1", job_id="job-1"):
            context = ServiceContext.from_current(actor_ref="user:42", idempotency_key="command-1")
        self.assertEqual(context.request_id, "request-1")
        self.assertEqual(context.correlation_id, "correlation-1")
        self.assertEqual(context.job_id, "job-1")
        self.assertEqual(context.actor_ref, "user:42")
        self.assertNotIn("command-1", repr(context))

    def test_service_actor_reference_is_opaque_and_never_email_or_url_shaped(self) -> None:
        for actor_ref in (
            "person@example.com",
            "user:person@example.com",
            "https://studio.example.invalid/users/42",
            "user:has whitespace",
            "service:ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "service:AKIAABCDEFGHIJKLMNOP",
            "service:eyJheader.payload.signature",
        ):
            with (
                self.subTest(actor_ref=actor_ref),
                self.assertRaisesRegex(ValueError, "Invalid actor_ref"),
            ):
                ServiceContext(correlation_id="correlation-1", actor_ref=actor_ref)
