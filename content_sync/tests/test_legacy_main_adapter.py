from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_sync.legacy_main import (
    LEGACY_MAIN_COMMIT,
    LEGACY_MAIN_CONTRACT,
    LEGACY_MAIN_TREE,
    ConferenceSourceRecord,
    LegacyMainAdapterContract,
    LegacyMainBundle,
    LegacyMainValidationError,
    LegacySelectedFile,
    ToolSourceRecord,
    parse_legacy_main_checkout,
)
from content_sync.legacy_main import adapter as adapter_module
from content_sync.legacy_main import contract as contract_module


def _pinned_checkout() -> Path:
    value = os.environ.get("LEGACY_MAIN_CHECKOUT")
    if value:
        path = Path(value)
        if path.is_dir():
            return path
    pytest.skip("set LEGACY_MAIN_CHECKOUT to the clean pinned legacy-main checkout")


def _copy_selected(source: Path, destination: Path) -> None:
    for selected in LEGACY_MAIN_CONTRACT.selected_files:
        target = destination / selected.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / selected.path, target)


def _codes(error: LegacyMainValidationError) -> tuple[str, ...]:
    return tuple(diagnostic.code for diagnostic in error.diagnostics)


def _diagnostic_shape(error: LegacyMainValidationError) -> tuple[dict[str, str], ...]:
    return tuple(item.as_dict() for item in error.diagnostics)


def _git_blob_sha(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def _manifest_sha(selected_files: tuple[LegacySelectedFile, ...]) -> str:
    manifest = "".join(
        f"{item.path}\t{item.git_blob_sha}\t{item.byte_size}\t{item.sha256}\n"
        for item in selected_files
    ).encode("utf-8")
    return hashlib.sha256(manifest).hexdigest()


def _contract_for_bytes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed_path: str,
    changed_bytes: bytes,
    body_evidence: tuple[tuple[str, int, str], ...] | None = None,
) -> LegacyMainAdapterContract:
    """Build a private test corpus contract while keeping the public contract fail-closed.

    Semantic mutation tests need to get past the source digest gate.  They patch the module-level
    canonical fixture for one test invocation, then call the same public parser with that fixture.
    Production callers cannot supply this replacement: the parser checks contract identity before
    invoking it, and the dedicated override tests below prove that boundary.
    """

    selected_files = tuple(
        replace(
            item,
            byte_size=len(changed_bytes),
            sha256=hashlib.sha256(changed_bytes).hexdigest(),
            git_blob_sha=_git_blob_sha(changed_bytes),
        )
        if item.path == changed_path
        else item
        for item in LEGACY_MAIN_CONTRACT.selected_files
    )
    if body_evidence is None:
        contract = replace(
            LEGACY_MAIN_CONTRACT,
            selected_files=selected_files,
            selected_manifest_sha256=_manifest_sha(selected_files),
            selected_bytes=sum(item.byte_size for item in selected_files),
        )
    else:
        contract = replace(
            LEGACY_MAIN_CONTRACT,
            selected_files=selected_files,
            selected_manifest_sha256=_manifest_sha(selected_files),
            selected_bytes=sum(item.byte_size for item in selected_files),
            body_evidence=body_evidence,
        )
    monkeypatch.setattr(contract_module, "LEGACY_MAIN_SELECTED_FILES", selected_files)
    monkeypatch.setattr(contract_module, "LEGACY_MAIN_CONTRACT", contract)
    monkeypatch.setattr(adapter_module, "LEGACY_MAIN_CONTRACT", contract)
    return contract


def _parse_with_test_contract(
    root: Path, contract: LegacyMainAdapterContract
) -> LegacyMainValidationError | LegacyMainBundle:
    try:
        return parse_legacy_main_checkout(
            root,
            origin=LEGACY_MAIN_CONTRACT.repository,
            commit=LEGACY_MAIN_CONTRACT.commit,
            tree=LEGACY_MAIN_CONTRACT.tree,
            contract=contract,
        )
    except LegacyMainValidationError as error:
        return error


def _assert_rejected_without_leaks(error: LegacyMainValidationError, *forbidden: str) -> None:
    assert error.diagnostics
    rendered = json.dumps(_diagnostic_shape(error), ensure_ascii=False)
    rendered += str(error)
    for value in forbidden:
        assert value not in rendered


def _minimal_checkout(source: Path, destination: Path) -> Path:
    _copy_selected(source, destination)
    return destination


def test_pinned_corpus_is_typed_deterministic_and_non_rendering(tmp_path: Path) -> None:
    source = _pinned_checkout()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _copy_selected(source, first_root)
    _copy_selected(source, second_root)

    first = parse_legacy_main_checkout(first_root)
    second = parse_legacy_main_checkout(second_root)

    assert first.as_dict() == second.as_dict()
    assert first.canonical_bytes == second.canonical_bytes
    assert first.bundle_sha256 == second.bundle_sha256
    assert first.provenance.commit == LEGACY_MAIN_COMMIT
    assert first.provenance.tree == LEGACY_MAIN_TREE
    assert dict(first.provenance.counts) == {
        "tools": 2,
        "conferences": 2,
        "tracks": 6,
        "talks": 26,
        "partners": 10,
        "asset_references": 12,
        "assets": 11,
        "person_references": 28,
        "person_keys": 27,
    }
    assert [tool.key for tool in first.tools] == ["modelstore", "obsei"]
    assert [conference.key for conference in first.conferences] == [
        "2021-feb",
        "2021-summer-marathon",
    ]
    assert [track.schedule_variant for track in first.conferences[0].tracks] == [
        "track_date_talk_time"
    ] * 4
    assert [track.schedule_variant for track in first.conferences[1].tracks] == [
        "track_window_talk_datetime"
    ] * 2
    assert first.conferences[1].tracks[0].talks[1].youtube is None
    assert first.conferences[1].tracks[0].talks[0].date == "2021-06-14 17:00:00"
    assert first.conferences[0].tracks[0].talks[0].time == "14:00"
    assert (
        sum(len(track.talks) for conference in first.conferences for track in conference.tracks)
        == 26
    )
    assert sum(len(conference.partners) for conference in first.conferences) == 10
    assert [asset.source_path for asset in first.assets] == sorted(
        asset.source_path for asset in first.assets
    )
    assert first.conferences[0].legacy_body_size == 3_111
    assert first.conferences[1].legacy_body_size == 3_866
    evidence = first.canonical_bytes.decode("utf-8")
    assert "javascript:" not in evidence
    assert "eventbrite.com/static/widgets" not in evidence
    assert "legacy_body" in evidence


def test_pinned_bundle_preserves_every_selected_provenance_and_reference_boundary(
    tmp_path: Path,
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "provenance")
    bundle = parse_legacy_main_checkout(root)
    records: dict[str, ToolSourceRecord | ConferenceSourceRecord] = {}
    for tool in bundle.tools:
        records[tool.source_path] = tool
    for conference in bundle.conferences:
        records[conference.source_path] = conference
    assets = {asset.source_path: asset for asset in bundle.assets}
    assert set(records) == {item.path for item in LEGACY_MAIN_CONTRACT.record_files}
    assert set(assets) == {item.path for item in LEGACY_MAIN_CONTRACT.asset_files}
    for selected_record in LEGACY_MAIN_CONTRACT.record_files:
        record_value = records[selected_record.path]
        assert (record_value.git_blob_sha, record_value.byte_size, record_value.sha256) == (
            selected_record.git_blob_sha,
            selected_record.byte_size,
            selected_record.sha256,
        )
    for selected_asset in LEGACY_MAIN_CONTRACT.asset_files:
        asset = assets[selected_asset.path]
        assert (asset.git_blob_sha, asset.byte_size, asset.sha256) == (
            selected_asset.git_blob_sha,
            selected_asset.byte_size,
            selected_asset.sha256,
        )
        assert asset.mime_family in {"image/jpeg", "image/png"}
        assert asset.references
    assert all(conference.layout == "page" for conference in bundle.conferences)
    assert all(
        "legacy_body" not in record.as_dict() or "<script>" not in json.dumps(record.as_dict())
        for record in bundle.conferences
    )
    assert all(
        not any(value.startswith("/") for value in record.maintainer_keys)
        for record in bundle.tools
    )


def test_selected_manifest_is_bound_to_all_four_records_and_eleven_assets() -> None:
    contract = LEGACY_MAIN_CONTRACT
    assert len(contract.record_files) == 4
    assert len(contract.asset_files) == 11
    assert sum(item.byte_size for item in contract.selected_files) == 314_358
    assert (
        hashlib.sha256(contract.selected_manifest).hexdigest() == contract.selected_manifest_sha256
    )
    assert contract.selected_manifest_sha256 == (
        "8a7ebde5ea63116efc4fe3651439ac7dceeb724609e0055332cddf3f22c84b6c"
    )


@pytest.mark.parametrize(
    ("label", "change"),
    (
        (
            "extra-selection",
            lambda files: (
                files
                + (
                    replace(
                        files[0],
                        path="_tools/synthetic-extra.md",
                        kind="excluded",
                    ),
                )
            ),
        ),
        (
            "traversal-selection",
            lambda files: (replace(files[0], path="../outside.md"), *files[1:]),
        ),
        (
            "case-selection",
            lambda files: (
                replace(files[0], path="_TOOLS/modelstore.md"),
                *files[1:],
            ),
        ),
        (
            "reordered-selection",
            lambda files: (files[1], files[0], *files[2:]),
        ),
        (
            "kind-selection",
            lambda files: (replace(files[0], kind="asset"), *files[1:]),
        ),
    ),
)
def test_public_contract_rejects_any_selection_override(
    tmp_path: Path,
    label: str,
    change: Callable[[tuple[LegacySelectedFile, ...]], tuple[LegacySelectedFile, ...]],
) -> None:
    del label
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "override")
    files = LEGACY_MAIN_CONTRACT.selected_files
    selected_files = change(files)
    overridden = replace(
        LEGACY_MAIN_CONTRACT,
        selected_files=selected_files,
        selected_manifest_sha256=_manifest_sha(selected_files),
    )

    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root, contract=overridden)

    assert _codes(raised.value) == ("legacy_main_parser_failed",)
    _assert_rejected_without_leaks(raised.value, "outside.md", "synthetic-extra")


def test_public_contract_rejects_noncanonical_limits_and_duck_types(
    tmp_path: Path,
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "override")
    relaxed = replace(LEGACY_MAIN_CONTRACT, max_diagnostics=200)
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root, contract=relaxed)
    assert _codes(raised.value) == ("legacy_main_parser_failed",)

    called = False

    def forbidden_validate() -> None:
        nonlocal called
        called = True
        raise AssertionError("replacement contract was invoked")

    replacement = SimpleNamespace(validate=forbidden_validate)
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root, contract=replacement)  # type: ignore[arg-type]
    assert _codes(raised.value) == ("legacy_main_parser_failed",)
    assert not called


def test_contract_validation_binds_paths_kinds_order_and_safe_relative_names() -> None:
    contract = LEGACY_MAIN_CONTRACT
    contract.validate()
    assert tuple(item.path for item in contract.selected_files) == tuple(
        sorted(item.path for item in contract.selected_files)
    )
    assert len({item.path for item in contract.selected_files}) == 15
    assert all(
        not item.path.startswith(("/", "\\"))
        and ".." not in Path(item.path).parts
        and "\\" not in item.path
        for item in contract.selected_files
    )


@pytest.mark.parametrize("relative", ["../outside.md", "/absolute.md", "images/../x.md"])
def test_private_reader_rejects_traversal_before_touching_files(
    tmp_path: Path, relative: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"do not read")
    root = tmp_path / "checkout"
    root.mkdir()
    selected = replace(LEGACY_MAIN_CONTRACT.selected_files[0], path=relative)
    diagnostics = adapter_module._Diagnostics(20)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("path traversal touched a file")
        ),
    )

    assert (
        adapter_module._read_selected(
            root,
            selected,
            contract=LEGACY_MAIN_CONTRACT,
            diagnostics=diagnostics,
        )
        is None
    )
    error = LegacyMainValidationError(diagnostics.finish())
    assert _codes(error) == ("source_path_escape",)
    assert outside.stat().st_size == len(b"do not read")


def test_metadata_and_dirty_checkout_fail_with_bounded_safe_diagnostics(tmp_path: Path) -> None:
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(
            tmp_path,
            origin="https://example.invalid/private",
            commit="not-a-sha",
            tree="not-a-tree",
            clean=False,
        )
    codes = _codes(raised.value)
    assert "source_origin_mismatch" in codes
    assert "source_revision_mismatch" in codes
    assert "source_tree_mismatch" in codes
    assert "source_checkout_dirty" in codes
    assert all("example.invalid" not in item.source_path for item in raised.value.diagnostics)
    assert all("not-a" not in item.pointer for item in raised.value.diagnostics)


def test_mutated_selected_record_is_rejected_atomically(tmp_path: Path) -> None:
    source = _pinned_checkout()
    root = tmp_path / "mutated"
    _copy_selected(source, root)
    target = root / "_tools" / "modelstore.md"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)
    assert "source_file_size_invalid" in _codes(raised.value)
    assert not hasattr(raised.value, "bundle")


@pytest.mark.parametrize(
    "relative",
    [item.path for item in LEGACY_MAIN_CONTRACT.selected_files],
)
def test_every_selected_blob_mutation_is_rejected_atomically(tmp_path: Path, relative: str) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "mutated")
    target = root / relative
    raw = bytearray(target.read_bytes())
    raw[-1] ^= 1
    target.write_bytes(bytes(raw))

    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)

    expected = (
        "asset_digest_mismatch" if relative.startswith("images/") else "source_digest_mismatch"
    )
    assert expected in _codes(raised.value)
    _assert_rejected_without_leaks(raised.value, str(target), str(tmp_path))


@pytest.mark.parametrize(
    ("relative", "line_prefix", "replacement", "expected_code"),
    (
        ("_tools/modelstore.md", b"title:", b"title: 17", "schema_value_type_invalid"),
        ("_tools/modelstore.md", b"title:", b"title: null", "schema_required_value_missing"),
        ("_tools/modelstore.md", b"title:", b"unknown_field: nope", "schema_field_unknown"),
        (
            "_tools/modelstore.md",
            b"github:",
            b"github: javascript:alert(1)",
            "url_scheme_forbidden",
        ),
        (
            "_conferences/2021-summer-marathon.md",
            b"start:",
            b"start: 2021-99-99 17:00:00",
            "schedule_value_invalid",
        ),
        (
            "_conferences/2021-summer-marathon.md",
            b"eventbrite:",
            b"eventbrite: 0",
            "eventbrite_id_invalid",
        ),
        (
            "_conferences/2021-summer-marathon.md",
            b"id:",
            b"id: ..",
            "person_key_invalid",
        ),
    ),
)
def test_semantic_source_mutations_fail_closed_with_stable_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    line_prefix: bytes,
    replacement: bytes,
    expected_code: str,
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "semantic")
    target = root / relative
    original = target.read_bytes()

    index = original.find(line_prefix)
    assert index >= 0, f"mutation marker not found in {relative}"
    line_end = original.find(b"\n", index)
    assert line_end >= 0
    current = original[index:line_end]
    assert len(replacement) <= len(current)
    replacement = replacement + b" " * (len(current) - len(replacement))
    mutated = original[:index] + replacement + original[line_end:]
    assert len(mutated) == len(original)
    target.write_bytes(mutated)
    contract = _contract_for_bytes(
        monkeypatch,
        changed_path=relative,
        changed_bytes=mutated,
    )
    result = _parse_with_test_contract(root, contract)
    assert isinstance(result, LegacyMainValidationError)
    assert expected_code in _codes(result)
    _assert_rejected_without_leaks(result, "javascript:alert", "../outside", str(tmp_path))


def test_mutated_conference_body_is_rejected_after_digest_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "body")
    target = root / "_conferences/2021-feb.md"
    raw = bytearray(target.read_bytes())
    raw[-1] ^= 1
    mutated = bytes(raw)
    target.write_bytes(mutated)
    contract = _contract_for_bytes(
        monkeypatch,
        changed_path=target.relative_to(root).as_posix(),
        changed_bytes=mutated,
    )
    result = _parse_with_test_contract(root, contract)
    assert isinstance(result, LegacyMainValidationError)
    assert "legacy_body_digest_mismatch" in _codes(result)
    _assert_rejected_without_leaks(result, str(tmp_path))


def test_digest_valid_wrong_asset_signature_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "asset-signature")
    relative = "images/partners/aicamp.png"
    target = root / relative
    mutated = b"not-a-png\n" + target.read_bytes()[10:]
    assert len(mutated) == target.stat().st_size
    target.write_bytes(mutated)
    contract = _contract_for_bytes(
        monkeypatch,
        changed_path=relative,
        changed_bytes=mutated,
    )
    result = _parse_with_test_contract(root, contract)
    assert isinstance(result, LegacyMainValidationError)
    assert "asset_type_invalid" in _codes(result)
    _assert_rejected_without_leaks(result, str(tmp_path))


@pytest.mark.parametrize("kind", ["missing", "executable", "symlink", "directory", "fifo"])
def test_selected_files_require_pinned_regular_checkout_entries(tmp_path: Path, kind: str) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / kind)
    target = root / "_tools/modelstore.md"
    if kind == "missing":
        target.unlink()
    elif kind == "executable":
        target.chmod(0o755)
    elif kind == "symlink":
        replacement = tmp_path / "outside.md"
        replacement.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(replacement)
    elif kind == "directory":
        target.unlink()
        target.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO entries are not available on this platform")
        target.unlink()
        os.mkfifo(target)

    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)

    expected = "source_path_missing" if kind == "missing" else "source_path_type_invalid"
    assert expected in _codes(raised.value)
    assert not hasattr(raised.value, "bundle")


@pytest.mark.parametrize("extra_name", ["unexpected.md", "MODELSTORE.md", "e\u0301.md"])
def test_collection_extra_case_or_normalization_entries_fail_without_reading_content(
    tmp_path: Path, extra_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "extra")
    extra = root / "_tools" / extra_name
    extra.write_bytes(b"attacker-controlled source that must not be read")
    original_read = Path.read_bytes

    def reject_extra(path: Path) -> bytes:
        if path == extra:
            raise AssertionError("unexpected collection entry was read")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", reject_extra)
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)
    assert "source_path_unexpected" in _codes(raised.value)
    _assert_rejected_without_leaks(raised.value, "attacker-controlled", str(tmp_path))


def test_unselected_assets_are_not_scanned_or_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "asset-exclusion")
    extra = root / "images/partners/not-selected.svg"
    extra.write_bytes(b"not a selected asset and must not be read")
    original_read = Path.read_bytes

    def reject_extra(path: Path) -> bytes:
        if path == extra:
            raise AssertionError("unselected asset was read")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", reject_extra)
    bundle = parse_legacy_main_checkout(root)
    assert extra.relative_to(root).as_posix() not in {asset.source_path for asset in bundle.assets}


def test_symlinked_collection_directory_is_rejected_without_following_it(
    tmp_path: Path,
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "collection-link")
    outside = tmp_path / "outside"
    outside.mkdir()
    original = root / "_tools"
    original.rename(root / "_tools-real")
    original.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)
    assert "source_path_type_invalid" in _codes(raised.value)
    assert not any(outside.iterdir())


def test_root_symlink_is_rejected_before_any_selected_read(tmp_path: Path) -> None:
    source = _pinned_checkout()
    real = _minimal_checkout(source, tmp_path / "real")
    link = tmp_path / "root-link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(link)
    assert _codes(raised.value) == ("source_path_type_invalid",)


def _yaml_error(text: str, *, map_it: bool = True) -> LegacyMainValidationError:
    diagnostics = adapter_module._Diagnostics(20)
    node = adapter_module._yaml_preflight(
        text,
        source_path="_tools/modelstore.md",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    mapping = None
    if map_it:
        mapping = adapter_module._mapping(
            node,
            source_path="_tools/modelstore.md",
            pointer="/",
            diagnostics=diagnostics,
        )
        if mapping and "value" in mapping:
            adapter_module._required_string(
                mapping,
                "value",
                source_path="_tools/modelstore.md",
                pointer="/",
                diagnostics=diagnostics,
                maximum=256,
            )
    return LegacyMainValidationError(diagnostics.finish())


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("value: &anchor text\nother: *anchor\n", "yaml_alias_forbidden"),
        ("value: &anchor text\nother: *anchor\n", "yaml_alias_forbidden"),
        ("base: &base\n  value: text\n<<: *base\n", "yaml_alias_forbidden"),
        ("!custom value\n", "schema_value_type_invalid"),
        ("1: value\n", "schema_value_type_invalid"),
        ("value: !!binary SGVsbG8=\n", "schema_value_type_invalid"),
        ("value: .inf\n", "schema_value_type_invalid"),
        ("value: [one, two\n", "frontmatter_invalid"),
    ),
)
def test_yaml_adversarial_inputs_are_rejected_with_fixed_codes(text: str, expected: str) -> None:
    error = _yaml_error(text)
    assert expected in _codes(error)
    _assert_rejected_without_leaks(error, "SGVsbG8", "anchor", "custom")


def test_yaml_depth_and_node_limits_are_bounded() -> None:
    depth_bomb = "".join(
        "  " * index + "value:\n" for index in range(LEGACY_MAIN_CONTRACT.max_yaml_depth + 2)
    )
    depth_bomb += "  " * (LEGACY_MAIN_CONTRACT.max_yaml_depth + 2) + "leaf: value\n"
    depth_error = _yaml_error(depth_bomb, map_it=False)
    assert "yaml_depth_exceeded" in _codes(depth_error)

    node_bomb = "".join(
        f"key_{index}: value\n" for index in range(LEGACY_MAIN_CONTRACT.max_yaml_nodes + 1)
    )
    node_error = _yaml_error(node_bomb)
    assert "yaml_node_limit_exceeded" in _codes(node_error)
    assert len(node_error.diagnostics) <= LEGACY_MAIN_CONTRACT.max_diagnostics


def test_invalid_utf8_and_frontmatter_framing_fail_before_value_parsing() -> None:
    diagnostics = adapter_module._Diagnostics(20)
    selected = LEGACY_MAIN_CONTRACT.record_files[0]
    assert (
        adapter_module._frontmatter_and_body(
            b"---\nvalue: \xff\n---\n",
            selected=selected,
            contract=LEGACY_MAIN_CONTRACT,
            diagnostics=diagnostics,
        )
        is None
    )
    assert "source_utf8_invalid" in _codes(LegacyMainValidationError(diagnostics.finish()))

    diagnostics = adapter_module._Diagnostics(20)
    assert (
        adapter_module._frontmatter_and_body(
            b"title: missing-delimiter\n",
            selected=selected,
            contract=LEGACY_MAIN_CONTRACT,
            diagnostics=diagnostics,
        )
        is None
    )
    assert "frontmatter_invalid" in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("<script>alert(1)</script>", "abstract_fragment_unsafe"),
        ("<br class='unsafe'>", "abstract_fragment_unsafe"),
        ("<img src='https://example.invalid/x'>", "abstract_fragment_unsafe"),
        ("{% include secret %}", "abstract_fragment_unsafe"),
        ("{{ secret }}", "abstract_fragment_unsafe"),
        ("javascript:alert(1)", "abstract_fragment_unsafe"),
        ("data:text/html,boom", "abstract_fragment_unsafe"),
        (" //evil.example/x", "abstract_fragment_unsafe"),
        ("<ul><li>safe</li></ul><br/>", "ok"),
    ),
)
def test_abstract_fragment_allowlist_is_inert_and_fail_closed(value: str, expected: str) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    mapping = {
        "abstract": adapter_module.ScalarNode("tag:yaml.org,2002:str", value),
    }
    result = adapter_module._abstract(
        mapping,
        "abstract",
        source_path="_conferences/2021-summer-marathon.md",
        pointer="/",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    assert result == value
    if expected == "ok":
        assert not diagnostics.finish()
    else:
        assert expected in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("http://example.invalid", "url_scheme_forbidden"),
        ("javascript:alert(1)", "url_scheme_forbidden"),
        ("//example.invalid/path", "url_scheme_forbidden"),
        ("https://user:pass@example.invalid/path", "url_invalid"),
        ("https://example.invalid/path\\x", "url_invalid"),
        ("https://example.invalid/path#fragment", "url_invalid"),
        ("https://example.invalid/path\nnext", "url_invalid"),
        ("https://example.invalid/path", "ok"),
    ),
)
def test_structured_urls_reject_unsafe_protocols_and_shapes(value: str, expected: str) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    node = adapter_module.ScalarNode("tag:yaml.org,2002:str", value)
    result = adapter_module._node_url(
        node,
        source_path="_tools/modelstore.md",
        pointer="/github",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    assert result == value
    if expected == "ok":
        assert not diagnostics.finish()
    else:
        assert expected in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("images/partners/aicamp.png", "ok"),
        ("/images/partners/aicamp.png", "ok"),
        ("images/other/../partners/aicamp.png", "asset_path_invalid"),
        ("images/partners/aicamp.png?x=1", "asset_path_invalid"),
        ("images/partners/aicamp.png#x", "asset_path_invalid"),
        ("//images/partners/aicamp.png", "asset_path_invalid"),
        ("images/partners/aicamp.png\\x", "asset_path_invalid"),
        ("images/private/aicamp.png", "asset_path_invalid"),
    ),
)
def test_asset_path_allowlist_is_root_relative_and_bounded(value: str, expected: str) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    result = adapter_module._asset_path(
        value,
        source_path="_conferences/2021-feb.md",
        pointer="/image",
        diagnostics=diagnostics,
        family="partners",
    )
    if expected == "ok":
        assert result == "/images/partners/aicamp.png"
        assert not diagnostics.finish()
    else:
        assert result == ""
        assert expected in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("2021-06-14 17:00:00", "ok"),
        ("2021-02-29 17:00:00", "schedule_value_invalid"),
        ("2021-06-14T17:00:00", "schedule_value_invalid"),
        ("2021-06-14 25:00:00", "schedule_value_invalid"),
        ("2021-06-14 17:00:00+00:00", "schedule_value_invalid"),
    ),
)
def test_date_lexemes_remain_naive_and_calendar_valid(value: str, expected: str) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    node = adapter_module.ScalarNode("tag:yaml.org,2002:str", value)
    mapping = {"date": node}
    result = adapter_module._date(
        mapping,
        "date",
        source_path="_conferences/2021-feb.md",
        pointer="/tracks/0",
        diagnostics=diagnostics,
    )
    if expected == "ok":
        assert result == value
        assert not diagnostics.finish()
    else:
        assert expected in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize(
    ("value", "style", "expected"),
    (
        ("14:00", '"', "ok"),
        ("14:00", None, "schedule_value_invalid"),
        ("2pm", '"', "schedule_value_invalid"),
        ("24:00", '"', "schedule_value_invalid"),
        ("14:60", '"', "schedule_value_invalid"),
    ),
)
def test_time_lexemes_require_quoted_hhmm_values(
    value: str, style: str | None, expected: str
) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    node = adapter_module.ScalarNode("tag:yaml.org,2002:str", value, style=style)
    result = adapter_module._time(
        {"time": node},
        "time",
        source_path="_conferences/2021-feb.md",
        pointer="/tracks/0/talks/0",
        diagnostics=diagnostics,
    )
    if expected == "ok":
        assert result == value
        assert not diagnostics.finish()
    else:
        assert expected in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "+1", "01", "1.0", "1e3", "true", "", "1/2"],
)
def test_eventbrite_identifiers_are_positive_opaque_decimal_strings(value: str) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    tag = "tag:yaml.org,2002:str"
    node = adapter_module.ScalarNode(tag, value)
    result = adapter_module._eventbrite(
        {"eventbrite": node},
        "eventbrite",
        source_path="_conferences/2021-feb.md",
        pointer="/tracks/0",
        diagnostics=diagnostics,
    )
    assert result == ""
    assert "eventbrite_id_invalid" in _codes(LegacyMainValidationError(diagnostics.finish()))


@pytest.mark.parametrize("value", ["", "../person", "person/email", "person\\x", "é"])
def test_person_keys_are_opaque_but_constrained(value: str) -> None:
    diagnostics = adapter_module._Diagnostics(20)
    node = adapter_module.ScalarNode("tag:yaml.org,2002:str", value)
    result = adapter_module._person(
        node,
        source_path="_conferences/2021-feb.md",
        pointer="/tracks/0/talks/0/speaker",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    assert result == ""
    assert "person_key_invalid" in _codes(LegacyMainValidationError(diagnostics.finish()))


def test_limits_and_diagnostics_overflow_are_bounded_and_deterministic() -> None:
    diagnostics = adapter_module._Diagnostics(3)
    for index in range(12):
        diagnostics.add("schema_value_type_invalid", f"_tools/{index}.md", "/unknown")
    first = diagnostics.finish()
    second = diagnostics.finish()
    assert first == second
    assert len(first) == 3
    assert first[-1].code == "diagnostics_overflow"
    assert first[-1].source_path == "."
    assert first[-1].pointer == "/"

    oversized = adapter_module._Diagnostics(20)
    selected = LEGACY_MAIN_CONTRACT.record_files[0]
    raw = b"---\n" + b"a" * (LEGACY_MAIN_CONTRACT.max_frontmatter_bytes + 1) + b"\n---\n"
    assert (
        adapter_module._frontmatter_and_body(
            raw,
            selected=selected,
            contract=LEGACY_MAIN_CONTRACT,
            diagnostics=oversized,
        )
        is None
    )
    assert "schema_limit_exceeded" in _codes(LegacyMainValidationError(oversized.finish()))


def test_collection_limits_and_missing_asset_references_fail_closed() -> None:
    diagnostics = adapter_module._Diagnostics(20)
    nodes = adapter_module._yaml_preflight(
        "talks:\n" + "".join("  - value\n" for _ in range(65)),
        source_path="_conferences/2021-feb.md",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    mapping = adapter_module._mapping(
        nodes,
        source_path="_conferences/2021-feb.md",
        pointer="/tracks/0",
        diagnostics=diagnostics,
    )
    adapter_module._sequence(
        mapping or {},
        "talks",
        source_path="_conferences/2021-feb.md",
        pointer="/tracks/0",
        diagnostics=diagnostics,
        maximum=LEGACY_MAIN_CONTRACT.max_talks,
    )
    assert "schema_limit_exceeded" in _codes(LegacyMainValidationError(diagnostics.finish()))

    diagnostics = adapter_module._Diagnostics(20)
    reference = adapter_module.AssetReference("conference", "2021-feb", "/image")
    assets = adapter_module._parse_assets(
        Path("/definitely/not/a/checkout"),
        selected_assets=LEGACY_MAIN_CONTRACT.asset_files[:1],
        references={"images/other/not-selected.png": (reference,)},
        contract=LEGACY_MAIN_CONTRACT,
        diagnostics=diagnostics,
    )
    assert assets == ()
    codes = _codes(LegacyMainValidationError(diagnostics.finish()))
    assert "asset_missing" in codes


def test_tool_bodies_are_empty_and_conference_body_evidence_is_not_renderable() -> None:
    diagnostics = adapter_module._Diagnostics(20)
    selected = LEGACY_MAIN_CONTRACT.record_files[0]
    result = adapter_module._body_ok(
        "modelstore",
        b"<script>unsafe</script>",
        selected=replace(selected, kind="tool"),
        contract=LEGACY_MAIN_CONTRACT,
        diagnostics=diagnostics,
    )
    assert result is None
    assert "legacy_body_digest_mismatch" in _codes(LegacyMainValidationError(diagnostics.finish()))


def test_bundle_values_are_immutable_and_no_partial_bundle_is_exposed(tmp_path: Path) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "immutable")
    bundle = parse_legacy_main_checkout(root)
    with pytest.raises((AttributeError, TypeError)):
        bundle.tools[0].name = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        bundle.provenance.counts["tools"] = 99  # type: ignore[index]

    target = root / "_tools/modelstore.md"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)
    assert not hasattr(raised.value, "bundle")


def test_exact_parse_has_no_network_subprocess_orm_write_or_registration_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "seams")
    original_read_bytes = Path.read_bytes
    before_bytes = {
        item.path: original_read_bytes(root / item.path)
        for item in LEGACY_MAIN_CONTRACT.selected_files
    }
    before_modules = set(sys.modules)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden external or write seam crossed")

    # Patch the standard-library seams even though the adapter intentionally does not import
    # their modules.  This catches a future indirect provider/ORM/runtime registration call.
    import socket
    import urllib.request

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        if hasattr(socket, name):
            monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    for name in ("write_bytes", "write_text", "touch", "unlink", "rename", "replace"):
        monkeypatch.setattr(Path, name, forbidden)

    # Django is loaded by the repository's test harness, but the adapter must never touch its
    # persistence or URL-registration surfaces.  Keep this optional so the pure package remains
    # runnable in a minimal Python environment as well.
    try:
        import django.urls as django_urls
        from django.db.models import Model, QuerySet
    except ImportError:
        pass
    else:
        for owner, names in (
            (Model, ("save", "delete")),
            (QuerySet, ("create", "update", "delete")),
        ):
            for name in names:
                if hasattr(owner, name):
                    monkeypatch.setattr(owner, name, forbidden)
        monkeypatch.setattr(django_urls, "path", forbidden)
        monkeypatch.setattr(django_urls, "include", forbidden)

    # Importing datetime ahead of the snapshot prevents its standard-library initialization from
    # looking like a parser registration side effect.
    import datetime  # noqa: F401

    bundle = parse_legacy_main_checkout(root)
    assert bundle.bundle_sha256 == (
        "ba73d1b0b34da677a109c1135044b837c93629746f70f3854e6e361606779a35"
    )
    assert {
        item.path: original_read_bytes(root / item.path)
        for item in LEGACY_MAIN_CONTRACT.selected_files
    } == before_bytes
    new_modules = set(sys.modules) - before_modules
    assert not any(
        name.startswith(("django", "content", "studio", "jobs", "management_api"))
        for name in new_modules
    )
    assert "subprocess" not in adapter_module.__dict__
    assert "socket" not in adapter_module.__dict__
    assert "urlopen" not in adapter_module.__dict__


def test_negative_parse_does_not_cross_hard_fail_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _pinned_checkout()
    root = _minimal_checkout(source, tmp_path / "negative-seams")
    target = root / "_tools/modelstore.md"
    target.write_bytes(target.read_bytes() + b"\n")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("negative parse crossed a forbidden seam")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(root)
    assert "source_file_size_invalid" in _codes(raised.value)


def test_import_boundary_does_not_register_routes_jobs_models_or_settings() -> None:
    assert "django" not in adapter_module.__dict__
    assert "urlpatterns" not in adapter_module.__dict__
    assert "models" not in adapter_module.__dict__
    assert "settings" not in adapter_module.__dict__
    assert "register" not in adapter_module.__dict__
    assert "job" not in adapter_module.__dict__


def test_import_and_failure_boundary_do_not_register_or_leak_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "subprocess" not in adapter_module.__dict__
    assert "socket" not in adapter_module.__dict__
    monkeypatch.setattr(
        adapter_module,
        "_parse",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret source value")),
    )
    with pytest.raises(LegacyMainValidationError) as raised:
        parse_legacy_main_checkout(Path("/nonexistent/checkout"))
    assert _codes(raised.value) == ("legacy_main_parser_failed",)
    assert "secret source value" not in str(raised.value)


def test_keyboard_interrupt_and_system_exit_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adapter_module,
        "_parse",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        parse_legacy_main_checkout(Path("/nonexistent/checkout"))
    monkeypatch.setattr(
        adapter_module,
        "_parse",
        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit()),
    )
    with pytest.raises(SystemExit):
        parse_legacy_main_checkout(Path("/nonexistent/checkout"))


def test_unsafe_fragment_tokens_and_yaml_aliases_are_rejected() -> None:
    diagnostics = adapter_module._Diagnostics(20)
    node = adapter_module._yaml_preflight(
        "value: &anchor text\nother: *anchor\n",
        source_path="_tools/modelstore.md",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    assert node is not None
    assert "yaml_alias_forbidden" in _codes(LegacyMainValidationError(diagnostics.finish()))
    diagnostics = adapter_module._Diagnostics(20)
    mapping = adapter_module._mapping(
        node,
        source_path="_tools/modelstore.md",
        pointer="/",
        diagnostics=diagnostics,
    )
    assert mapping is not None
    adapter_module._abstract(
        {
            "abstract": adapter_module.ScalarNode(
                "tag:yaml.org,2002:str", "<script>alert(1)</script>"
            )
        },
        "abstract",
        source_path="_conferences/2021-feb.md",
        pointer="/",
        diagnostics=diagnostics,
        contract=LEGACY_MAIN_CONTRACT,
    )
    assert "abstract_fragment_unsafe" in _codes(LegacyMainValidationError(diagnostics.finish()))
