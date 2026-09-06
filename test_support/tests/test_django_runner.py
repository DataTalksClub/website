"""The parallel runner must report a failure it cannot pickle, not die on it."""

from __future__ import annotations

import pickle
import unittest

from django.test import SimpleTestCase

from test_support.django_runner import (
    IsolatedDiscoverRunner,
    ResilientRemoteTestResult,
    ResilientRemoteTestRunner,
)
from test_support.parallel_failures import (
    UnpicklableAssertionFailure,
    UnpicklableFailure,
    picklable_exc_info,
)


class Unshippable(AssertionError):
    """Deliberately unpicklable: pickling looks the class up by name and fails."""

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("cannot pickle 'Unshippable' object")


class UnshippableRuntime(RuntimeError):
    """Unpicklable, and not an assertion, so it must stay an error."""

    def __reduce__(self) -> tuple[object, ...]:
        raise TypeError("cannot pickle 'UnshippableRuntime' object")


def _exc_info(exception: BaseException) -> tuple[type[BaseException], BaseException, object]:
    try:
        raise exception
    except BaseException as raised:  # noqa: BLE001 - the triple is the subject
        return type(raised), raised, raised.__traceback__


class PicklableExcInfoTests(SimpleTestCase):
    def test_a_picklable_triple_is_passed_through_untouched(self) -> None:
        err = (AssertionError, AssertionError("plain"), None)

        self.assertIs(picklable_exc_info("suite.Case.test_one", err), err)

    def test_an_unpicklable_triple_becomes_formatted_text(self) -> None:
        err = _exc_info(Unshippable("the real assertion nobody could read"))

        replacement = picklable_exc_info("suite.Case.test_one", err)

        self.assertIs(replacement[0], UnpicklableAssertionFailure)
        self.assertIsNone(replacement[2])
        message = str(replacement[1])
        self.assertIn("suite.Case.test_one", message)
        self.assertIn("the real assertion nobody could read", message)
        self.assertIn("Traceback (most recent call last)", message)
        self.assertIn("test_support/tests/test_django_runner.py", message)

    def test_an_assertion_stays_a_failure_and_anything_else_stays_an_error(self) -> None:
        assertion = picklable_exc_info("suite.Case.test_one", _exc_info(Unshippable("no")))
        other = picklable_exc_info("suite.Case.test_two", _exc_info(UnshippableRuntime("no")))

        self.assertTrue(issubclass(assertion[0], AssertionError))
        self.assertFalse(issubclass(other[0], AssertionError))
        self.assertIs(other[0], UnpicklableFailure)

    def test_the_replacement_survives_a_pickle_round_trip(self) -> None:
        replacement = picklable_exc_info(
            "suite.Case.test_one",
            _exc_info(Unshippable("the real assertion nobody could read")),
        )

        restored = pickle.loads(pickle.dumps(replacement))

        self.assertEqual(str(restored[1]), str(replacement[1]))


class ResilientRemoteRunTests(SimpleTestCase):
    def test_the_worker_ships_the_failure_instead_of_raising(self) -> None:
        # Defined here, not at module level, so discovery never runs it for real.
        class FailingCase(unittest.TestCase):
            def runTest(self) -> None:
                raise Unshippable("the real assertion nobody could read")

        suite = unittest.TestSuite([FailingCase()])

        result = ResilientRemoteTestRunner().run(suite)
        events = pickle.loads(pickle.dumps(result.events))

        recorded = [event for event in events if event[0] in {"addError", "addFailure"}]
        self.assertEqual(len(recorded), 1)
        message = str(recorded[0][2][1])
        self.assertIn("FailingCase.runTest", message)
        self.assertIn("the real assertion nobody could read", message)

    def test_the_isolated_runner_uses_the_resilient_result(self) -> None:
        suite_class = IsolatedDiscoverRunner.parallel_test_suite

        self.assertIs(suite_class.runner_class.resultclass, ResilientRemoteTestResult)
