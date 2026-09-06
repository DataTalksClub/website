from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import connections
from django.db.backends.sqlite3.creation import DatabaseCreation
from django.test.runner import (
    DiscoverRunner,
    ParallelTestSuite,
    RemoteTestResult,
    RemoteTestRunner,
)

from .email_backend import SYNTHETIC_EMAIL_BACKEND, reset_capture_mailbox
from .network import NetworkGuard
from .parallel_failures import ExcInfo, picklable_exc_info
from .reference_data import load_reviewed_reference_data
from .runtime import TestRuntime, TestRuntimeSafetyError


class IsolatedSQLiteCreation(DatabaseCreation):
    def get_test_db_clone_settings(self, suffix: str) -> dict[str, Any]:
        runtime: TestRuntime = settings.TEST_RUNTIME
        layout = runtime.worker(f"django-{suffix}")
        path = runtime.assert_database_path(layout.database, worker_id=layout.worker_id)
        return {
            **self.connection.settings_dict,
            "NAME": path,
            "TEST": {**self.connection.settings_dict.get("TEST", {}), "NAME": path},
        }


class ResilientRemoteTestResult(RemoteTestResult):
    """Never let an unpicklable failure take the whole parallel run down.

    Django ships each worker failure to the main process as a pickled
    ``exc_info`` triple and re-raises whatever pickling error it hits, which
    aborts every remaining test and prints no assertion.  Replacing the triple
    with formatted text first means the run reports the failing test and its
    traceback and carries on to the next one.
    """

    def addError(self, test: Any, err: ExcInfo) -> None:
        super().addError(test, picklable_exc_info(test, err))

    def addFailure(self, test: Any, err: ExcInfo) -> None:
        super().addFailure(test, picklable_exc_info(test, err))

    def addExpectedFailure(self, test: Any, err: ExcInfo) -> None:
        super().addExpectedFailure(test, picklable_exc_info(test, err))

    def addSubTest(self, test: Any, subtest: Any, err: ExcInfo | None) -> None:
        if err is not None:
            err = picklable_exc_info(test, err)
        super().addSubTest(test, subtest, err)


class ResilientRemoteTestRunner(RemoteTestRunner):
    resultclass = ResilientRemoteTestResult


class ResilientParallelTestSuite(ParallelTestSuite):
    runner_class = ResilientRemoteTestRunner


class IsolatedDiscoverRunner(DiscoverRunner):
    """Django runner with owned SQLite clones and default external-network denial."""

    parallel_test_suite = ResilientParallelTestSuite
    _network_guard: NetworkGuard | None = None

    def setup_test_environment(self, **kwargs: Any) -> None:
        self._validate_connections()
        self._network_guard = NetworkGuard()
        self._network_guard.__enter__()
        try:
            super().setup_test_environment(**kwargs)
            settings.EMAIL_BACKEND = SYNTHETIC_EMAIL_BACKEND
            reset_capture_mailbox()
        except BaseException:
            self._network_guard.__exit__(None, None, None)
            self._network_guard = None
            raise

    def teardown_test_environment(self, **kwargs: Any) -> None:
        try:
            super().teardown_test_environment(**kwargs)
        finally:
            if self._network_guard is not None:
                self._network_guard.__exit__(None, None, None)
                self._network_guard = None
            connections.close_all()
            settings.TEST_RUNTIME.cleanup()

    def setup_databases(self, **kwargs: Any) -> Any:
        self._validate_connections()
        for connection in connections.all():
            connection.creation.__class__ = IsolatedSQLiteCreation
        old_config = super().setup_databases(**kwargs)
        # A run whose selected tests need no database gets no database: Django
        # reports "Skipping setup of unused database(s)" and returns nothing.
        # Only the aliases it actually built can be written to.
        built = {connection.alias for connection, _old_name, _destroy in old_config}
        for connection in connections.all():
            if connection.alias not in built:
                continue
            load_reviewed_reference_data()
            # ``create_test_db`` snapshots the database for ``serialized_rollback``
            # before this runs, so a test that restores from that snapshot would
            # otherwise come back without the reference rows.  The attribute is
            # Django-private and untyped, hence the dynamic read/write.
            if getattr(connection, "_test_serialized_contents", None) is not None:
                setattr(  # noqa: B010
                    connection,
                    "_test_serialized_contents",
                    connection.creation.serialize_db_to_string(),
                )
        return old_config

    @staticmethod
    def _validate_connections() -> None:
        for connection in connections.all():
            if connection.vendor != "sqlite":
                raise TestRuntimeSafetyError("ordinary Django tests require isolated SQLite")
            runtime: TestRuntime = settings.TEST_RUNTIME
            runtime.assert_database_path(
                connection.settings_dict["NAME"],
                worker_id=connection.settings_dict.get("DTC_WORKER_ID", "main"),
            )
