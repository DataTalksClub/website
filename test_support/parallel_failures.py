"""Report a parallel-worker failure even when its ``exc_info`` cannot be pickled.

``manage.py test --parallel`` sends every failure from a worker process back to
the main process by pickling the ``sys.exc_info()`` triple.  A traceback object
is not picklable, and neither is an exception that holds a connection, a socket
or a locally defined class, so :meth:`django.test.runner.RemoteTestResult.check_picklable`
re-raises the pickling error.  That error escapes the worker, ``imap_unordered``
propagates it, and the *entire* run dies with something like
``TypeError: cannot pickle 'traceback' object`` -- with no sign of which test
failed or why.

The failure text is what a reader needs, and text always crosses a process
boundary.  So the moment an ``exc_info`` triple proves unpicklable we format it
in the worker, where the live traceback still exists, and send back a plain
:class:`UnpicklableFailure` carrying that text.  The run then reports the real
test id and the real traceback, and keeps going.
"""

from __future__ import annotations

import pickle
import traceback
from types import TracebackType

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


class UnpicklableFailure(Exception):
    """A worker failure rendered as text because its ``exc_info`` would not pickle.

    Takes exactly one already-formatted argument so that the default
    ``Exception`` pickling round-trips it.
    """


def describe_test(test: object) -> str:
    identify = getattr(test, "id", None)
    if callable(identify):
        try:
            return str(identify())
        except Exception:  # pragma: no cover - defensive, ``id()`` is trivial
            pass
    return str(test)


def format_unpicklable_failure(test: object, err: ExcInfo, pickle_error: BaseException) -> str:
    """Render a failure the worker cannot ship as an object into one it can."""

    formatted = "".join(traceback.format_exception(err[0], err[1], err[2]))
    return (
        f"{describe_test(test)} failed, and the failure could not be sent back "
        f"from the parallel worker process ({pickle_error!r}).\n"
        f"The traceback below was formatted inside the worker.\n\n{formatted}"
    )


def picklable_exc_info(test: object, err: ExcInfo) -> ExcInfo:
    """Return ``err`` when it survives a pickle round trip, else a text stand-in."""

    try:
        pickle.loads(pickle.dumps(err))
    except Exception as pickle_error:
        message = format_unpicklable_failure(test, err, pickle_error)
        return (UnpicklableFailure, UnpicklableFailure(message), None)
    return err

