"""Which database a ``scripts/prod`` entry point writes, and what it takes to aim one
at production.

Every entry point that writes rows used to carry its own four-line ``_configure``, and
all thirteen copies said exactly the same thing: ``DTC_ENVIRONMENT=local``,
``DTC_SQLITE_PATH=<--database>``, ``website.settings.local``.  There was no second
route, so the production data migration -- which
``_docs/runbooks/production-data-migration.md`` describes as the same commands with a
different ``$TARGET`` -- could only ever write to a scratch SQLite file.  This package
is the missing route and the single place the rule now lives.

Two selections, and neither is a default
----------------------------------------

``--database PATH``
    A local SQLite file.  Unchanged: the same three environment values, set in the
    same order and with the same precedence, so every Make target, rehearsal command
    and test behaves exactly as it did.

``--deployment-target NAME``
    A reviewed deployed database.  ``NAME`` is looked up in
    ``deploy.deployment_targets.DEPLOYMENT_TARGETS`` -- the same closed allowlist the
    release pipeline selects from -- and a retired target is refused, so the only
    name that resolves today is ``website-production``.

Exactly one of the two is required.  Neither has a default, and neither can be
supplied by the ambient environment, so no run reaches a deployed database unless
someone typed that database's name.

What a deployed run inherits, and from where
--------------------------------------------

Nothing here invents a way to address production.  A deployed target's non-secret
environment is exactly ``DeploymentTarget.fixed_nonsecret_environment`` -- the mapping
``deploy/task_definitions.py`` requires of the deployed web, worker and migration
containers.  An import therefore runs with the settings module, allowed hosts,
canonical origin, media store and *deliberately inert Datamailer client* the deployed
task runs with, rather than with a hand-assembled approximation that can drift from
it.

The secrets are not in this repository and never pass through this process.  ECS
injects ``DATABASE_URL`` and ``DJANGO_SECRET_KEY`` from Secrets Manager; an operator
supplies the same two names in the environment.  This module checks only that the
*names* are set and refuses by name when they are not.  It never reads, prints or
logs a value, so a refusal is safe to paste into a run log.

``VERSION``, ``SOURCE_SHA`` and ``IMAGE_DIGEST`` are required for the same reason:
``core.runtime_identity`` refuses to start any deployed process without a sealed
release identity, and that refusal is a feature -- production settings are meant to
boot inside a real release, not in an improvised shell.  Checking the three names
here turns an opaque ``RuntimeError`` raised part-way through settings import into a
refusal that says which value is missing.

Why a production write takes two flags that have to agree
---------------------------------------------------------

``--deployment-target NAME`` selects, and ``--allow-production-write NAME`` opts in
and has to repeat the same name.  One flag alone would have been a ritual: the
runbook is where operators copy commands from, so whatever a production command block
contains gets pasted along with it.  Two flags that must name the same target buy a
real property instead -- a block half-edited from one target to another disagrees
with itself and is refused, and the operator has to have typed the production
target's name twice.

Three further defaults keep an accident from arriving by a side door:

* the opt-in is refused alongside ``--database``, so it cannot sit in a rehearsal
  command as a harmless-looking leftover that later gains a target;
* a shell that already exports a deployed ``DTC_ENVIRONMENT`` or
  ``DJANGO_SETTINGS_MODULE`` -- the plausible state of an operator's terminal during
  a migration -- cannot promote a ``--database`` run.  That combination is refused by
  name instead of being reinterpreted, which is what used to happen until
  ``require_environment`` failed with ``environment-mismatch`` several frames later;
  and
* ``DTC_SQLITE_PATH`` is cleared for a deployed run, so a scratch path left over from
  a rehearsal cannot describe where the rows went.

None of this changes what any importer writes.  It changes only where the rows land.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.bootstrap import RuntimeEnvironment

#: The settings module a local run falls back to, exactly as ``manage.py`` does.
LOCAL_SETTINGS_MODULE = "website.settings.local"
LOCAL_TARGET_NAME = "local"

#: Environments whose database holds real member data.
DEPLOYED_ENVIRONMENTS = frozenset({RuntimeEnvironment.DEVELOPMENT, RuntimeEnvironment.PRODUCTION})
#: ``website/settings/`` is named after the environment it serves, so the deployed
#: settings modules follow from the deployed environments rather than being relisted.
DEPLOYED_SETTINGS_MODULES = frozenset(
    f"website.settings.{environment.value}" for environment in DEPLOYED_ENVIRONMENTS
)

#: Names a deployed run needs and this repository cannot supply: the two secrets ECS
#: injects from Secrets Manager, and the sealed release identity every deployed
#: process must carry.  Checked for presence only -- never read.
REQUIRED_DEPLOYED_ENVIRONMENT = (
    "DATABASE_URL",
    "DJANGO_SECRET_KEY",
    "VERSION",
    "SOURCE_SHA",
    "IMAGE_DIGEST",
)


class TargetRefused(ValueError):
    """A refusal that names the unmet condition and never a value."""


@dataclass(frozen=True, slots=True)
class WriteTarget:
    """The database one entry-point run will write, and how it was selected."""

    name: str
    deployed: bool
    #: Exported before Django starts.  Non-secret by construction.
    forced: Mapping[str, str]
    #: Exported only where the shell has not already chosen a value.
    defaulted: Mapping[str, str] = field(default_factory=dict)
    #: Removed, so a value describing another run cannot describe this one.
    cleared: tuple[str, ...] = ()

    def apply(self, environ: MutableMapping[str, str]) -> None:
        for name in self.cleared:
            environ.pop(name, None)
        environ.update(self.forced)
        for name, value in self.defaulted.items():
            environ.setdefault(name, value)


def add_target_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the target selection every row-writing entry point shares."""

    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--database",
        metavar="PATH",
        type=Path,
        help="write to this local SQLite file",
    )
    selection.add_argument(
        "--deployment-target",
        metavar="NAME",
        help="write to a reviewed deployed database instead of a local file",
    )
    parser.add_argument(
        "--allow-production-write",
        metavar="NAME",
        default=None,
        help="opt in to writing to NAME; must repeat --deployment-target exactly",
    )


def _deployable_target_names() -> str:
    from deploy.deployment_targets import DEPLOYMENT_TARGETS

    return ", ".join(
        sorted(name for name, target in DEPLOYMENT_TARGETS.items() if not target.retired)
    )


def _reviewed_target(name: str) -> Any:
    from deploy.deployment_targets import DEPLOYMENT_TARGETS

    target = DEPLOYMENT_TARGETS.get(name)
    if target is None:
        raise TargetRefused(
            "--deployment-target must name a reviewed deployment target; "
            f"deployable targets are: {_deployable_target_names()}"
        )
    if target.retired:
        raise TargetRefused(f"deployment target {name} is retired and cannot be written to")
    return target


def _refuse_ambient_deployment(environ: Mapping[str, str]) -> None:
    deployed_environments = {environment.value for environment in DEPLOYED_ENVIRONMENTS}
    if (
        environ.get("DTC_ENVIRONMENT") in deployed_environments
        or environ.get("DJANGO_SETTINGS_MODULE") in DEPLOYED_SETTINGS_MODULES
    ):
        raise TargetRefused(
            "the shell already selects a deployed environment; unset DTC_ENVIRONMENT and "
            "DJANGO_SETTINGS_MODULE for a --database run, or name the database with "
            "--deployment-target"
        )


def resolve_target(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
) -> WriteTarget:
    """Decide where this run writes, refusing anything short of a complete selection."""

    values = os.environ if environ is None else environ
    database = getattr(args, "database", None)
    deployment_target = getattr(args, "deployment_target", None)
    opt_in = getattr(args, "allow_production_write", None)

    if deployment_target is None:
        if database is None:
            raise TargetRefused("a target is required: --database PATH or --deployment-target NAME")
        if opt_in is not None:
            raise TargetRefused(
                "--allow-production-write applies to --deployment-target, not to a local --database"
            )
        _refuse_ambient_deployment(values)
        return WriteTarget(
            name=LOCAL_TARGET_NAME,
            deployed=False,
            forced={
                "DTC_ENVIRONMENT": RuntimeEnvironment.LOCAL.value,
                "DTC_SQLITE_PATH": str(Path(database).resolve()),
            },
            defaulted={"DJANGO_SETTINGS_MODULE": LOCAL_SETTINGS_MODULE},
        )

    target = _reviewed_target(deployment_target)
    if opt_in is None:
        raise TargetRefused(
            f"writing to the {target.name} database requires --allow-production-write {target.name}"
        )
    if opt_in != target.name:
        raise TargetRefused("--allow-production-write must repeat the selected --deployment-target")
    absent = [name for name in REQUIRED_DEPLOYED_ENVIRONMENT if not values.get(name)]
    if absent:
        raise TargetRefused(
            f"the {target.name} environment is incomplete; these names are unset: "
            + ", ".join(absent)
        )
    return WriteTarget(
        name=target.name,
        deployed=True,
        forced=dict(target.fixed_nonsecret_environment),
        cleared=("DTC_SQLITE_PATH",),
    )


def configure_target(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    environ: MutableMapping[str, str] | None = None,
) -> WriteTarget:
    """Select the target, export its environment and start Django.

    A refusal is reported through ``parser.error``: usage, one line naming the unmet
    condition, exit status 2, and no traceback for an operator to read past.
    """

    values = os.environ if environ is None else environ
    try:
        target = resolve_target(args, values)
    except TargetRefused as refusal:
        parser.error(str(refusal))
    target.apply(values)

    import django

    django.setup()
    return target


def configure_ambient_settings() -> None:
    """Start Django for an entry point that has no database of its own.

    These read and write an object store, never the ORM, so they respect whatever
    ``DJANGO_SETTINGS_MODULE`` is already set -- production included, since a deployed
    publish or verify needs real settings -- and fall back to local exactly as
    ``manage.py`` does.
    """

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", LOCAL_SETTINGS_MODULE)

    import django

    django.setup()


__all__ = [
    "DEPLOYED_ENVIRONMENTS",
    "DEPLOYED_SETTINGS_MODULES",
    "LOCAL_SETTINGS_MODULE",
    "LOCAL_TARGET_NAME",
    "REQUIRED_DEPLOYED_ENVIRONMENT",
    "TargetRefused",
    "WriteTarget",
    "add_target_arguments",
    "configure_ambient_settings",
    "configure_target",
    "resolve_target",
]
