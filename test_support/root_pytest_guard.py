"""Structural boundary between unified pytest and the copied CMP E2E suite.

The root ``pyproject.toml`` loads this plugin explicitly. Additive command-line
and ``PYTEST_ADDOPTS`` options, including ``--confcutdir`` and ``--rootdir``, remain protected.
Replacing the configured ``addopts`` or explicitly disabling this plugin is an
operator decision outside this boundary. An arbitrary in-process plugin can
also intentionally unregister or corrupt other plugins, so that is not claimed
as a supported threat boundary.
"""

from __future__ import annotations

import posixpath
from collections.abc import Generator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UNIFIED_PYTEST_CONFIG = REPOSITORY_ROOT / "pyproject.toml"
UNIFIED_COPIED_E2E_DENIAL = (
    "copied CMP E2E tests cannot run under the unified root pytest configuration; "
    "use the standalone e2e/pytest.ini configuration"
)


@dataclass(frozen=True, slots=True)
class _ItemProvenance:
    node_path: str | None
    source_paths: tuple[str, ...]


_ORIGINAL_PROVENANCE = pytest.StashKey[_ItemProvenance]()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_itemcollected(item: pytest.Item) -> Generator[None]:
    """Snapshot provenance before ordinary item-collected hooks can mutate it."""
    item.stash[_ORIGINAL_PROVENANCE] = _capture_provenance(item)
    yield


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    _reject_selected_copied_e2e(session.config, session.items)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    del nextitem
    _reject_selected_copied_e2e(item.config, (item,))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    _reject_selected_copied_e2e(item.config, (item,))


def _reject_selected_copied_e2e(
    config: pytest.Config,
    items: Sequence[pytest.Item],
) -> None:
    if not _uses_unified_config(config):
        return
    if any(_is_copied_e2e_item(item) for item in items):
        raise pytest.UsageError(UNIFIED_COPIED_E2E_DENIAL)


def _uses_unified_config(config: pytest.Config) -> bool:
    inipath = config.inipath
    return inipath is not None and Path(inipath).resolve() == UNIFIED_PYTEST_CONFIG


def _is_copied_e2e_item(item: pytest.Item) -> bool:
    original = item.stash.get(_ORIGINAL_PROVENANCE, None)
    current = _capture_provenance(item)
    provenances = (current,) if original is None else (original, current)
    return any(_is_copied_e2e_provenance(provenance) for provenance in provenances)


def _capture_provenance(item: pytest.Item) -> _ItemProvenance:
    paths: list[str] = []
    for collector in item.listchain():
        _append_resolved_path(paths, getattr(collector, "path", None))
    _append_resolved_path(paths, getattr(item, "path", None))
    module = getattr(item, "module", None)
    _append_resolved_path(paths, getattr(module, "__file__", None))
    return _ItemProvenance(
        node_path=_normalized_node_path(item.nodeid),
        source_paths=tuple(dict.fromkeys(paths)),
    )


def _append_resolved_path(paths: list[str], value: object) -> None:
    if value is None:
        return
    try:
        paths.append(Path(str(value)).resolve().as_posix())
    except (OSError, RuntimeError, ValueError):
        return


def _normalized_node_path(nodeid: str) -> str | None:
    raw_path = nodeid.partition("::")[0].replace("\\", "/")
    normalized = posixpath.normpath(raw_path)
    path = PurePosixPath(normalized)
    if normalized in {"", "."} or path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _is_copied_e2e_provenance(provenance: _ItemProvenance) -> bool:
    if provenance.node_path == "e2e" or (provenance.node_path or "").startswith("e2e/"):
        return True
    for source in provenance.source_paths:
        try:
            relative = Path(source).relative_to(REPOSITORY_ROOT)
        except ValueError:
            continue
        if relative.parts[:1] == ("e2e",):
            return True
    return False
