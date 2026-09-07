"""Static reachability from a ``SimpleTestCase`` body to a database read.

``SimpleTestCase`` is a boundary marker: it refuses the database, so a class that
carries it is a written claim that the unit under test reads no row. That claim
went stale when the course catalogue moved into the database underneath
``content_sync`` (#324), and the wall was only ever observed where a test
actually ran. Two classes in this package are gated behind
``DTC_CONTENT_ACCEPTED_CHECKOUT`` / ``DTC_CONTENT_BASELINE_CHECKOUT``, which no
default environment and no CI job sets, so their claim was never tested at all.

This module answers the question by reading the source instead of running it, so
the check holds under a plain ``manage.py test content_sync`` with no checkout,
no fixtures and no network. It resolves the first-party callables a class
reaches -- across modules, through module-level helpers, sibling methods, and
names bound by an import written inside a function body -- and reports the chain
whenever one of them touches ``content.catalogue`` or a model manager.

The analysis is deliberately an over-approximation: it follows every call it can
resolve and ignores control flow. A class whose executed path fails closed before
the database read is therefore expected to carry the chain and the reason in
``VERDICTS`` in ``test_database_boundary.py``, rather than be tuned away here.

What it does not resolve is indirection, where the callee is not statically
knowable: ``getattr(module, name)()``, a callable pulled out of a dict or list,
and methods reached through an instance of a helper class other than the test
class itself. Those are blind spots by decision, not oversight -- resolving them
means guessing at a name the source does not state.

A statically bound name is a different matter and is followed wherever it is
written, function bodies included -- but the resolution is frame granular, not
scope exact. ``_local_imports`` flattens a method with ``ast.walk``, so an import
written in a nested function binds for the whole enclosing method and can shadow
a module-level name: after ``def _inner(): from hashlib import sha256 as
published_catalogue``, the frame resolves ``published_catalogue`` to
``hashlib.sha256``, a leaf, and no chain is reported. That stays open on purpose.
It needs one identifier bound at module level to a first-party reader *and*
re-bound in a nested scope of the same method, which is written nowhere in this
repository, whereas the plain function-body import is the ordinary idiom (live at
``content_sync/tests/test_course_repository_transport_parity.py:237``). A scope
tree would close it at the cost of the nested function's own local import, which
flattening catches today: a poor trade for a shape nobody writes.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _first_party_roots() -> frozenset[str]:
    """Top-level packages of this repository, read from the checkout.

    Only these are followed. Everything else -- stdlib, requests, yaml, django
    itself -- is a leaf: it reaches our catalogue only through a callable we
    already resolved on the way in. Deriving the set from the checkout rather
    than listing it means a new app is covered the day it is added, and a
    removed one cannot linger here as a name that resolves to nothing.
    """

    return frozenset(
        entry.name
        for entry in PROJECT_ROOT.iterdir()
        if entry.is_dir() and (entry / "__init__.py").is_file()
    )


FIRST_PARTY_ROOTS = _first_party_roots()

# The two database signals. ``content.catalogue`` is the reader the catalogue
# migration put in front of every published-content read; ``.objects`` is the
# manager on any model. Both are attribute accesses, so they survive renames of
# the callables around them.
CATALOGUE_MODULE = "content.catalogue"
MANAGER_ATTRIBUTE = "objects"

CATALOGUE_SIGNAL = "catalogue read"
MANAGER_SIGNAL = "model manager access (.objects)"

MAX_DEPTH = 12


@dataclass(frozen=True)
class Reach:
    """One resolved path from a ``SimpleTestCase`` method to a database read."""

    test_class: str
    entry: str
    chain: tuple[str, ...]
    signal: str

    def described(self) -> str:
        return f"{' -> '.join(self.chain)}  [{self.signal}]"


@dataclass(frozen=True)
class _Target:
    """A resolved module-level name: a module, or a name defined inside one."""

    module: str
    name: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.module}.{self.name}" if self.name else self.module


def _module_path(module: str) -> Path | None:
    relative = Path(*module.split("."))
    for candidate in (
        PROJECT_ROOT / relative.with_suffix(".py"),
        PROJECT_ROOT / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _is_first_party(module: str) -> bool:
    return module.split(".", 1)[0] in FIRST_PARTY_ROOTS


@cache
def _parsed(module: str) -> ast.Module | None:
    path = _module_path(module)
    if path is None:
        return None
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_of(module: str) -> str:
    path = _module_path(module)
    if path is not None and path.name == "__init__.py":
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def _absolute_import(module: str, imported: str, level: int) -> str:
    """Absolute module for ``from .imported import ...`` written inside ``module``."""

    if not level:
        return imported
    package = _package_of(module)
    for _step in range(level - 1):
        package = package.rsplit(".", 1)[0] if "." in package else ""
    if not package:
        return imported
    return f"{package}.{imported}" if imported else package


def _bind_import(
    module: str, node: ast.Import | ast.ImportFrom, bindings: dict[str, _Target]
) -> None:
    """Record the names ``node`` binds, wherever in ``module`` it is written."""

    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                bindings[alias.asname] = _Target(alias.name)
            else:
                root = alias.name.split(".", 1)[0]
                bindings[root] = _Target(root)
        return
    source = _absolute_import(module, node.module or "", node.level)
    for alias in node.names:
        bound = alias.asname or alias.name
        submodule = f"{source}.{alias.name}" if source else alias.name
        if _module_path(submodule) is not None:
            bindings[bound] = _Target(submodule)
        else:
            bindings[bound] = _Target(source, alias.name)


@cache
def _namespace(module: str) -> dict[str, _Target]:
    """Module-level names bound by an import or a definition, mapped to targets."""

    tree = _parsed(module)
    if tree is None:
        return {}
    bindings: dict[str, _Target] = {}
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            _bind_import(module, node, bindings)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bindings[node.name] = _Target(module, node.name)
    return bindings


def _local_imports(module: str, node: ast.stmt) -> dict[str, _Target]:
    """Names bound by an import written *inside* ``node``.

    ``def run_pull(self): from scripts.prod.sync_course_repositories import pull``
    is already a live idiom in this package
    (``content_sync/tests/test_course_repository_transport_parity.py:237``), and
    a module-level walk cannot see it. A test method that imports a catalogue
    reader in its own body would resolve to no target at all, so the class would
    pass this guard and still die on the database wall when it runs.

    This is name resolution, not indirection: the callee is written in plain
    sight and is resolved here by the same code that handles the identical
    import one scope up, aliases and cross-module descent included. Bindings
    found here shadow the module-level ones for the frame that owns them, and
    are not carried into a callee's frame.
    """

    bindings: dict[str, _Target] = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Import | ast.ImportFrom):
            _bind_import(module, child, bindings)
    return bindings


@cache
def _definition(module: str, name: str) -> ast.stmt | None:
    tree = _parsed(module)
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == name:
                return node
    return None


@cache
def _function(module: str, name: str) -> ast.stmt | None:
    """The module-level function ``name``, or ``None`` if it is not one.

    Classes are leaves. Descending into every method of a referenced class made
    a type annotation (``contract: DtcContentAdapterContract = ...``) look like a
    call into that class' database work, which reports a chain nobody wrote. A
    class that really does reach a row does so through its manager, and
    ``.objects`` is caught wherever it is written.
    """

    definition = _definition(module, name)
    if isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef):
        return definition
    return None


class _Analysis:
    """The traversal for one test class.

    ``siblings`` are that class' own methods, so ``self.helper()`` is followed
    like any other call. Each path carries the definitions it has already
    entered, which is what makes a recursive or mutually recursive call graph
    terminate without losing a distinct second route to the same function.
    """

    def __init__(self, module: str, siblings: Mapping[str, ast.stmt]) -> None:
        self.module = module
        self.siblings = siblings

    def reaches(
        self,
        node: ast.stmt,
        *,
        module: str,
        namespace: dict[str, _Target],
        chain: tuple[str, ...],
        visited: frozenset[tuple[str, str]],
        depth: int,
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        if depth > MAX_DEPTH:
            return
        local = _local_imports(module, node)
        scope = {**namespace, **local} if local else namespace
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute):
                yield from self._from_attribute(
                    child, scope=scope, chain=chain, visited=visited, depth=depth
                )
            elif isinstance(child, ast.Name):
                target = scope.get(child.id)
                if target is None or not target.name:
                    continue
                if target.module == CATALOGUE_MODULE:
                    yield (*chain, target.qualified), CATALOGUE_SIGNAL
                elif _is_first_party(target.module):
                    yield from self._descend(target, chain=chain, visited=visited, depth=depth)

    def _from_attribute(
        self,
        child: ast.Attribute,
        *,
        scope: dict[str, _Target],
        chain: tuple[str, ...],
        visited: frozenset[tuple[str, str]],
        depth: int,
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        value = child.value
        if isinstance(value, ast.Name) and value.id == "self":
            sibling = self.siblings.get(child.attr)
            if sibling is not None:
                key = (self.module, f"self.{child.attr}")
                if key not in visited:
                    yield from self.reaches(
                        sibling,
                        module=self.module,
                        namespace=_namespace(self.module),
                        chain=(*chain, f"self.{child.attr}"),
                        visited=visited | {key},
                        depth=depth + 1,
                    )
            return
        if child.attr == MANAGER_ATTRIBUTE:
            yield chain, MANAGER_SIGNAL
            return
        if not isinstance(value, ast.Name):
            return
        target = scope.get(value.id)
        if target is None or target.name:
            return
        if target.module == CATALOGUE_MODULE:
            yield (*chain, f"{CATALOGUE_MODULE}.{child.attr}"), CATALOGUE_SIGNAL
        elif _is_first_party(target.module):
            yield from self._descend(
                _Target(target.module, child.attr), chain=chain, visited=visited, depth=depth
            )

    def _descend(
        self,
        target: _Target,
        *,
        chain: tuple[str, ...],
        visited: frozenset[tuple[str, str]],
        depth: int,
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        key = (target.module, target.name)
        if key in visited:
            return
        definition = _function(target.module, target.name)
        if definition is None:
            return
        yield from self.reaches(
            definition,
            module=target.module,
            namespace=_namespace(target.module),
            chain=(*chain, target.qualified),
            visited=visited | {key},
            depth=depth + 1,
        )


def simple_test_case_classes(module: str) -> dict[str, ast.ClassDef]:
    """``SimpleTestCase`` subclasses defined at module level in ``module``."""

    tree = _parsed(module)
    if tree is None:
        return {}
    namespace = _namespace(module)
    found: dict[str, ast.ClassDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if not isinstance(base, ast.Name) or base.id != "SimpleTestCase":
                continue
            target = namespace.get(base.id)
            if target is not None and target.qualified == "django.test.SimpleTestCase":
                found[node.name] = node
    return found


def database_reaches(module: str) -> tuple[Reach, ...]:
    """Database reads reachable from every ``SimpleTestCase`` method in ``module``."""

    namespace = _namespace(module)
    reaches: dict[tuple[str, tuple[str, ...], str], Reach] = {}
    for class_name, node in simple_test_case_classes(module).items():
        methods = {
            child.name: child
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        analysis = _Analysis(module, methods)
        for method_name, method in sorted(methods.items()):
            for chain, signal in analysis.reaches(
                method,
                module=module,
                namespace=namespace,
                chain=(f"{class_name}.{method_name}",),
                visited=frozenset(),
                depth=0,
            ):
                # One report per resolved call path. ``published_catalogue``
                # reads six catalogue functions in a row; six identical chains
                # differing only in the last hop is noise, not six findings.
                path = chain[:-1] if signal == CATALOGUE_SIGNAL else chain
                reaches.setdefault(
                    (class_name, path, signal),
                    Reach(
                        test_class=class_name,
                        entry=chain[1] if len(chain) > 1 else chain[0],
                        chain=chain,
                        signal=signal,
                    ),
                )
    return tuple(reaches.values())


def test_modules() -> tuple[str, ...]:
    """Every test module in this package, as an importable dotted name."""

    package = Path(__file__).resolve().parent
    return tuple(sorted(f"content_sync.tests.{path.stem}" for path in package.glob("test_*.py")))
