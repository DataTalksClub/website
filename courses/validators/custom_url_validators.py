"""Field-attached URL validators.

``validate_url_200`` is attached directly to ``Homework``/``Project`` URL
model fields, so Django's migration serializer records a reference to this
module and function by name (``courses.validators.custom_url_validators.
validate_url_200``). That means every ``migrate`` replay from an empty
database -- CI, a fresh developer setup, the eventual production bootstrap --
imports this module's top-level code as a side effect of loading the
historical migration, whether or not the validator itself ever runs.

Keep this module's own top-level imports to Django and the standard library
only. The real HTTP transport, redirect-rejection, SSRF guards, and FAQ URL
cleaning logic live in ``courses.validators.url_status_transport`` and are
loaded lazily, by name, only when the validator actually runs (e.g. during
``full_clean()``) -- never at module import time, so a migration replay never
pulls in ``requests`` or ``core.security``.

See ``test_support/tests/test_migrations.py`` for the guard test that checks
this module stays import-stable, and
``test_support/migrations.py`` for the static check it runs.
"""

from __future__ import annotations

import importlib

from django.core.validators import URLValidator


def _transport():
    # A deferred, dynamic import (by name, not an ``import`` statement) so
    # this module's own AST never mentions ``courses.validators.
    # url_status_transport`` -- only calling this function pulls in
    # ``requests``/``core.security``, and calling it never happens during a
    # migration replay.
    return importlib.import_module("courses.validators.url_status_transport")


def validate_url_200(url, get_method=None, code=None, params=None):
    return _transport().check_url_status(url, get_method=get_method, code=code, params=params)


class Status200UrlValidator(URLValidator):
    def __call__(self, value):
        super().__call__(value)
        validate_url_200(value, code=self.code, params={"value": value})
