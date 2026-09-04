"""Versioned, immutable selection contract for the first legacy-main parser slice."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

LEGACY_MAIN_REPOSITORY = "https://github.com/DataTalksClub/datatalksclub.github.io"
LEGACY_MAIN_COMMIT = "ee43d3fa0929faf691178d79f19528e6f15a83e5"
LEGACY_MAIN_TREE = "1666d11893a9f001e916409f3bac65a9ef0ff072"
LEGACY_MAIN_PARSER_VERSION = "legacy-main-v1"
LEGACY_MAIN_SCHEMA_VERSION = 1

LEGACY_MAIN_MAX_RECORD_BYTES = 32 * 1024
LEGACY_MAIN_MAX_ASSET_BYTES = 128 * 1024
LEGACY_MAIN_MAX_SOURCE_BYTES = 512 * 1024
LEGACY_MAIN_MAX_FRONTMATTER_BYTES = 24 * 1024
LEGACY_MAIN_MAX_LEGACY_BODY_BYTES = 8 * 1024
LEGACY_MAIN_MAX_YAML_DEPTH = 16
LEGACY_MAIN_MAX_YAML_NODES = 2_048
LEGACY_MAIN_MAX_DIAGNOSTICS = 20
LEGACY_MAIN_MAX_TRACKS = 64
LEGACY_MAIN_MAX_TALKS = 64
LEGACY_MAIN_MAX_PARTNERS = 32
LEGACY_MAIN_MAX_URL_LENGTH = 2_048
LEGACY_MAIN_MAX_OPAQUE_KEY_LENGTH = 200
LEGACY_MAIN_MAX_TITLE_LENGTH = 256
LEGACY_MAIN_MAX_NAME_LENGTH = 256
LEGACY_MAIN_MAX_COMPANY_LENGTH = 256
LEGACY_MAIN_MAX_CATEGORY_LENGTH = 256
LEGACY_MAIN_MAX_DESCRIPTION_LENGTH = 4_096
LEGACY_MAIN_MAX_ABSTRACT_LENGTH = 16_384


@dataclass(frozen=True, slots=True)
class LegacySelectedFile:
    """One selected regular blob in the immutable legacy snapshot."""

    path: str
    git_blob_sha: str
    byte_size: int
    sha256: str
    kind: str


_RECORDS = (
    LegacySelectedFile(
        path="_conferences/2021-feb.md",
        git_blob_sha="75d7c2f9c0e3acdfd18c0cfe66b41bdcbdd39d97",
        byte_size=18_261,
        sha256="df494d6bcfbd02b7b0745e75f149eb6029f6c249b5b6c6df29a349bc4e19b77b",
        kind="conference",
    ),
    LegacySelectedFile(
        path="_conferences/2021-summer-marathon.md",
        git_blob_sha="3a541ddec2d81d4e1fb8495cbca3c6f6333b03d4",
        byte_size=11_089,
        sha256="db239c7ff07b6bebceae144fbf588eb92d53e26a57ff4db457d17f03b564f71d",
        kind="conference",
    ),
    LegacySelectedFile(
        path="_tools/modelstore.md",
        git_blob_sha="de13d7d514af7e8187d54ec0ce73b5d0b33453ac",
        byte_size=256,
        sha256="9a66d4fba23b1616d1126ee1b892e97f25e5124389db6025ae2838dc41d18131",
        kind="tool",
    ),
    LegacySelectedFile(
        path="_tools/obsei.md",
        git_blob_sha="bcfb109854ea6b92f41a11568eac8b5fbe098e45",
        byte_size=246,
        sha256="13fe3b6e0b873add7fb9da83a2e2c0e175be54dfaebe1f5cd2f3255d5a9ed027",
        kind="tool",
    ),
)

_ASSETS = (
    LegacySelectedFile(
        path="images/other/conference-2021-feb-cover.jpg",
        git_blob_sha="538b8f819e0fec88b5b7e8f87cd0201b69324b84",
        byte_size=41_644,
        sha256="8354a6e53e06d227f3b1d291f1d2abb96e7112fd2ec42e146cea3d142eb5b333",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/other/conference-2021-summer-marathon-cover.jpg",
        git_blob_sha="b245b80a9d31a35df7c68012ee39f1c00b1e24c3",
        byte_size=101_807,
        sha256="ad6bfe867d477d3f5415ce69620127060036900eb92cfdc625c1c27ab6f0dd83",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/aicamp.png",
        git_blob_sha="921c9be8fb88e440fc81f05c2cd3e9e2269ebadd",
        byte_size=34_290,
        sha256="690d35cd88677dd2cbb292ad23dc4bf17929125c565437d52e8632f76177acf4",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/confetti.png",
        git_blob_sha="04854a28b8070eaf37854b664212cf634d50c193",
        byte_size=10_859,
        sha256="720f4dca0dff44fdb1919f0ffe2ac52b7abf7a3c6e7c0be068a7c84bf6f66635",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/dphi.png",
        git_blob_sha="891bd1efe77a5cd0e76fb3dc7413848422fc65c3",
        byte_size=9_484,
        sha256="7cbfc95fc1c43f3c2a44dcb730de9dc5c61bcadd73508f8655a9f827e35f8373",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/mlops-community.jpg",
        git_blob_sha="1551e92e19ccebf7476fa3ffbca03eb9319c3af8",
        byte_size=10_863,
        sha256="32a3fa7eabe88f25bde91382d691b25f500b4befe440b152ce55a5cf9d4fc678",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/odsc.png",
        git_blob_sha="c881cdfd70a5c69c5adfb37b9e9ae21ce3ad8005",
        byte_size=17_435,
        sha256="e1641a1bbaf28ead0dc1edaa824d02ad6da8aa719b5299cdd19e216cdce8dbe9",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/oreilly.jpg",
        git_blob_sha="13e7959e3bc8063c43c545af5817319631ba3e5f",
        byte_size=11_815,
        sha256="21841b32991025990810707e5e97b84daf0ad57bcc386c2d5dd0474b9bcd0ee7",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/pydata-london.png",
        git_blob_sha="76f636d791516c4f10ee57d824fcfcddd17af882",
        byte_size=12_083,
        sha256="ae2fff85ac104b2d4058105bfd93368aee407e2d365d902937b8275eacd0d7f6",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/thenewstack.png",
        git_blob_sha="7c85efa0a784ee39cad0a28f99d1772c3d32574c",
        byte_size=15_884,
        sha256="b850bc6e59da36eb350fa0a2198de022d402ef720be68537715244a6ad90c478",
        kind="asset",
    ),
    LegacySelectedFile(
        path="images/partners/thesequence.png",
        git_blob_sha="0378326d4778663076df4991eaf41a7eaf2b097a",
        byte_size=18_342,
        sha256="a58cf7c9ff06dfe6b0e7a5d4c4d619441074cb80446ce55418314ec1f7334d85",
        kind="asset",
    ),
)

LEGACY_MAIN_SELECTED_FILES = tuple(sorted((*_RECORDS, *_ASSETS), key=lambda item: item.path))
LEGACY_MAIN_SELECTED_MANIFEST_SHA256 = (
    "8a7ebde5ea63116efc4fe3651439ac7dceeb724609e0055332cddf3f22c84b6c"
)
LEGACY_MAIN_SELECTED_BYTES = 314_358

LEGACY_MAIN_BODY_EVIDENCE = {
    "2021-feb": (3_111, "336a008c02a4bdbb13a76ecf11358ef555270ff822ed797944cc3efbffe366c8"),
    "2021-summer-marathon": (
        3_866,
        "bcdcb8359fe49e66342566fd01a91735709ef876208d1f5c11a8bfc5ed717ac2",
    ),
}

LEGACY_MAIN_EXPECTED_COUNTS = {
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

LEGACY_MAIN_EXPECTED_PERSON_KEYS = (
    "neallathia",
    "lalitpagaria",
    "philwinder",
    "himanshuupreti",
    "mahmoudaziz",
    "eugeneyan",
    "eliasnema",
    "susanwalsh",
    "elenasamuylova",
    "danbecker",
    "dannyma",
    "admondleekinlim",
    "parulpandey",
    "vinvashishta",
    "larysavisengeriyeva",
    "mikiobraun",
    "sararobinson",
    "elleobrien",
    "svpino",
    "dalianaliu",
    "andreaskretz",
    "roksolanadiachuk",
    "elenasamuylova",
    "janzawadzki",
    "benwilson",
    "linaweichbrodt",
    "dougturnbull",
    "fabianaclemente",
)


@dataclass(frozen=True, slots=True)
class LegacyMainAdapterContract:
    """All mutable-looking parser assumptions are bound in this value."""

    repository: str = LEGACY_MAIN_REPOSITORY
    commit: str = LEGACY_MAIN_COMMIT
    tree: str = LEGACY_MAIN_TREE
    parser_version: str = LEGACY_MAIN_PARSER_VERSION
    schema_version: int = LEGACY_MAIN_SCHEMA_VERSION
    selected_files: tuple[LegacySelectedFile, ...] = LEGACY_MAIN_SELECTED_FILES
    selected_manifest_sha256: str = LEGACY_MAIN_SELECTED_MANIFEST_SHA256
    selected_bytes: int = LEGACY_MAIN_SELECTED_BYTES
    max_record_bytes: int = LEGACY_MAIN_MAX_RECORD_BYTES
    max_asset_bytes: int = LEGACY_MAIN_MAX_ASSET_BYTES
    max_source_bytes: int = LEGACY_MAIN_MAX_SOURCE_BYTES
    max_frontmatter_bytes: int = LEGACY_MAIN_MAX_FRONTMATTER_BYTES
    max_legacy_body_bytes: int = LEGACY_MAIN_MAX_LEGACY_BODY_BYTES
    max_yaml_depth: int = LEGACY_MAIN_MAX_YAML_DEPTH
    max_yaml_nodes: int = LEGACY_MAIN_MAX_YAML_NODES
    max_diagnostics: int = LEGACY_MAIN_MAX_DIAGNOSTICS
    max_tracks: int = LEGACY_MAIN_MAX_TRACKS
    max_talks: int = LEGACY_MAIN_MAX_TALKS
    max_partners: int = LEGACY_MAIN_MAX_PARTNERS
    max_url_length: int = LEGACY_MAIN_MAX_URL_LENGTH
    max_opaque_key_length: int = LEGACY_MAIN_MAX_OPAQUE_KEY_LENGTH
    max_title_length: int = LEGACY_MAIN_MAX_TITLE_LENGTH
    max_name_length: int = LEGACY_MAIN_MAX_NAME_LENGTH
    max_company_length: int = LEGACY_MAIN_MAX_COMPANY_LENGTH
    max_category_length: int = LEGACY_MAIN_MAX_CATEGORY_LENGTH
    max_description_length: int = LEGACY_MAIN_MAX_DESCRIPTION_LENGTH
    max_abstract_length: int = LEGACY_MAIN_MAX_ABSTRACT_LENGTH
    body_evidence: tuple[tuple[str, int, str], ...] = tuple(
        (key, size, digest) for key, (size, digest) in sorted(LEGACY_MAIN_BODY_EVIDENCE.items())
    )
    expected_counts: tuple[tuple[str, int], ...] = tuple(LEGACY_MAIN_EXPECTED_COUNTS.items())
    expected_person_keys: tuple[str, ...] = LEGACY_MAIN_EXPECTED_PERSON_KEYS

    @property
    def record_files(self) -> tuple[LegacySelectedFile, ...]:
        return tuple(item for item in self.selected_files if item.kind in {"tool", "conference"})

    @property
    def asset_files(self) -> tuple[LegacySelectedFile, ...]:
        return tuple(item for item in self.selected_files if item.kind == "asset")

    @property
    def selected_manifest(self) -> bytes:
        return "".join(
            f"{item.path}\t{item.git_blob_sha}\t{item.byte_size}\t{item.sha256}\n"
            for item in self.selected_files
        ).encode("utf-8")

    def validate(self) -> None:
        # The parser's public contract is deliberately not an extension point.  A caller may
        # provide provenance metadata (origin, commit, tree, and clean status), but may not
        # replace the selected corpus, relax a limit, change a schema, or inject a different
        # diagnostics policy through ``contract=``.  Comparing the complete frozen value keeps
        # all of those assumptions fail closed, including fields that are not part of the
        # selected-file manifest.
        if type(self.selected_files) is not tuple:
            raise ValueError("legacy-main selected file allowlist contract is invalid")
        if any(type(item) is not LegacySelectedFile for item in self.selected_files):
            raise ValueError("legacy-main selected file allowlist contract is invalid")
        if self.selected_files != LEGACY_MAIN_SELECTED_FILES:
            raise ValueError("legacy-main selected file allowlist contract is invalid")

        # Keep this explicit path check in addition to tuple equality.  It documents the
        # traversal/collision boundary and protects the invariant if the canonical tuple is ever
        # edited incorrectly.
        seen_paths: set[str] = set()
        for item in self.selected_files:
            path = item.path
            if type(path) is not str:
                raise ValueError("legacy-main selected file path contract is invalid")
            posix = PurePosixPath(path)
            if (
                not path
                or path != posix.as_posix()
                or posix.is_absolute()
                or any(part in {"", ".", ".."} for part in posix.parts)
                or "\\" in path
                or "\x00" in path
                or path in seen_paths
            ):
                raise ValueError("legacy-main selected file path contract is invalid")
            seen_paths.add(path)

        # Do not permit a semantically equal-looking dataclass with a different field set to
        # become a second source of truth.  The identity check at the public adapter boundary
        # below rejects such values; equality here also makes direct ``validate()`` calls useful
        # and catches every non-selection override.
        if "LEGACY_MAIN_CONTRACT" in globals() and self != LEGACY_MAIN_CONTRACT:
            raise ValueError("legacy-main contract override is not permitted")
        manifest = self.selected_manifest
        if hashlib.sha256(manifest).hexdigest() != self.selected_manifest_sha256:
            raise ValueError("legacy-main selected manifest contract is invalid")
        if sum(item.byte_size for item in self.selected_files) != self.selected_bytes:
            raise ValueError("legacy-main selected byte contract is invalid")
        if len(self.record_files) != 4 or len(self.asset_files) != 11:
            raise ValueError("legacy-main selected file contract is incomplete")


LEGACY_MAIN_CONTRACT = LegacyMainAdapterContract()
LEGACY_MAIN_CONTRACT.validate()
