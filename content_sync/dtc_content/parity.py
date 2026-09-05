from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import quote

from content import catalogue
from content.inventory import content_route_contracts
from content.podcast_resources import normalize_podcast_resources
from content.services import PreparedDocument
from scripts.build_public_projection import (
    _article_blocks,
    _canonical_podcast_platform_key,
    _canonical_podcast_platform_url,
)
from scripts.build_public_projection import _string as _projection_string
from scripts.projection_build.public_projection_source import DEFAULT_PROJECTION_ROOT

from .adapter import CandidateAsset, CandidateBundle, CandidateRelation, DtcContentValidationError
from .contract import (
    ACCEPTED_ADOPTED_SOURCE_SET_SHA256,
    ACCEPTED_COMPARISON_SHA256,
    ACCEPTED_CONTENT_COMMIT,
    ACCEPTED_CONTENT_TREE,
    ACCEPTED_COUNTS,
    EDITORIAL_OVERLAY_SHA256,
    PROJECTION_MANIFEST_SHA256,
    PROJECTION_PODCASTS_SHA256,
    PROJECTION_TREE_SHA256,
    REPAIR_COMPLETION_REFERENCE,
    REPAIR_MANIFEST_SHA256,
    REPAIRED_BASELINE_CI_RUN,
    REPAIRED_BASELINE_COMMIT,
    REPAIRED_BASELINE_TREE,
    REPLACEMENT_ATTESTATION_SHA256,
    SOURCE_CI_RUN,
)

_DECLARED_TRANSFORMS = (
    "database release UUIDs, sequences, revisions, and immutable storage keys are database-only",
    "article Markdown is compared through the checked #105 block projection and source checksum",
    "five exact accepted-pin remote article images absent from #105 blocks are omitted from "
    "rendered HTML under code-pinned source checksums",
    "podcast transcripts remain separate documents while #105 embeds their ordered segments",
    (
        "podcast provider keys and legacy Spotify-host destinations are canonicalized at "
        "projection boundary"
    ),
    "Person display names and paths are #105 presentation expansion over exact relation keys",
    "projection collection ordering is presentation-only; adapter records use stable key order",
    "database public-path identities use checked percent encoding where #105 stores "
    "a raw source path",
)


@dataclass(frozen=True, slots=True)
class ProjectionParityEvidence:
    status: str
    projection_manifest_sha256: str
    projection_tree_sha256: str
    projection_podcasts_sha256: str
    adopted_source_set_sha256: str
    comparison_sha256: str
    counts: Mapping[str, int]
    declared_transforms: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "projection_manifest_sha256": self.projection_manifest_sha256,
            "projection_tree_sha256": self.projection_tree_sha256,
            "projection_podcasts_sha256": self.projection_podcasts_sha256,
            "adopted_source_set_sha256": self.adopted_source_set_sha256,
            "comparison_sha256": self.comparison_sha256,
            "counts": dict(self.counts),
            "declared_transforms": list(self.declared_transforms),
        }


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _fail(code: str, source_path: str = ".") -> NoReturn:
    raise DtcContentValidationError(code, source_path)


def _expect(actual: Any, expected: Any, *, code: str, source_path: str) -> None:
    if actual != expected:
        _fail(code, source_path)


def _positive_integer(value: Any, *, code: str, source_path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(code, source_path)
    return value


def _structured(document: PreparedDocument) -> dict[str, Any]:
    try:
        value = json.loads(document.raw_structured_data)
    except json.JSONDecodeError:
        _fail("projection_structured_data_invalid", document.source_path)
    if not isinstance(value, dict):
        _fail("projection_structured_data_invalid", document.source_path)
    return value


def _relation_keys(
    relations: Sequence[CandidateRelation],
    *,
    source_kind: str,
    source_key: str,
    relation_type: str,
) -> list[str]:
    selected = [
        relation
        for relation in relations
        if relation.source_kind == source_kind
        and relation.source_key == source_key
        and relation.relation_type == relation_type
    ]
    selected.sort(key=lambda relation: relation.order)
    if [relation.order for relation in selected] != list(range(len(selected))):
        _fail("projection_relation_order_mismatch")
    return [relation.target_key for relation in selected]


def _provenance(
    document: PreparedDocument,
    *,
    repository: str,
    revision: str,
) -> dict[str, str]:
    return {
        "repository": repository,
        "revision": revision,
        "source_path": document.source_path,
        "source_key": document.stable_key,
        "checksum": document.checksum,
        "source_url": (f"https://github.com/{repository}/blob/{revision}/{document.source_path}"),
    }


def _common_document(
    document: PreparedDocument,
    record: Mapping[str, Any],
    *,
    seo_title: str,
    summary: str,
    seo_description: str,
    image_path: str,
) -> None:
    source_path = document.source_path
    _expect(
        document.exact_public_path,
        record["public_path"],
        code="projection_path_mismatch",
        source_path=source_path,
    )
    _expect(document.slug, record["slug"], code="projection_slug_mismatch", source_path=source_path)
    _expect(
        document.title, record["title"], code="projection_title_mismatch", source_path=source_path
    )
    _expect(document.summary, summary, code="projection_summary_mismatch", source_path=source_path)
    _expect(
        document.canonical_url,
        f"https://datatalks.club{record['public_path']}",
        code="projection_canonical_mismatch",
        source_path=source_path,
    )
    _expect(
        document.seo_title, seo_title, code="projection_seo_title_mismatch", source_path=source_path
    )
    _expect(
        document.seo_description,
        seo_description,
        code="projection_seo_description_mismatch",
        source_path=source_path,
    )
    _expect(
        document.seo_image_url,
        f"https://datatalks.club{quote(image_path, safe='/')}",
        code="projection_seo_image_mismatch",
        source_path=source_path,
    )
    _expect(
        document.is_published, True, code="projection_publish_mismatch", source_path=source_path
    )
    _expect(document.noindex, False, code="projection_noindex_mismatch", source_path=source_path)


def _expect_contract_identity(
    record: PreparedDocument | CandidateAsset,
    *,
    public_path: str,
    contracts: Mapping[str, Any],
    expect_asset: bool,
    source_path: str,
) -> bool:
    contract = contracts.get(public_path)
    if contract is None:
        expected = (None, None, None)
    else:
        if (contract.contract_kind == "asset") != expect_asset:
            _fail("projection_contract_kind_mismatch", source_path)
        expected = (contract.contract_id, contract.source_id, contract.source_revision)
    actual = (
        record.contract_id,
        record.contract_source_id,
        record.contract_source_revision,
    )
    _expect(
        actual,
        expected,
        code="projection_contract_identity_mismatch",
        source_path=source_path,
    )
    return contract is not None


def _published_date(document: PreparedDocument, record: Mapping[str, Any]) -> None:
    published = str(record.get("published") or "")
    if not published:
        _expect(
            document.source_created_at,
            None,
            code="projection_date_mismatch",
            source_path=document.source_path,
        )
        return
    if (
        document.source_created_at is None
        or document.source_created_at.date().isoformat() != published[:10]
    ):
        _fail("projection_date_mismatch", document.source_path)


def _comparable_article_blocks(blocks: Any) -> Any:
    """Return article body blocks without the sizes only a checked-out file can give.

    An illustration block carries the intrinsic pixel size the build read from the
    image itself, so the page can reserve its box.  The adapter bundle transports
    that image as bytes and a checksum, not as a tree this comparison can open, and
    those bytes are already compared asset by asset below.  Everything else about a
    block — its kind, its order, its text and its source segment — is compared
    exactly, which is what proves the two readings of the same Markdown agree.
    """

    if not isinstance(blocks, (list, tuple)):
        return blocks
    return [
        (
            {key: value for key, value in block.items() if key not in {"width", "height"}}
            if isinstance(block, dict) and block.get("kind") == "image"
            else block
        )
        for block in blocks
    ]


def _transcript_segments(value: Any, *, source_path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("projection_transcript_mismatch", source_path)
    result: list[dict[str, Any]] = []
    for segment in value:
        if not isinstance(segment, dict):
            _fail("projection_transcript_mismatch", source_path)
        if segment.get("header"):
            result.append(
                {
                    "header": _projection_string(
                        segment["header"],
                        field="podcast transcript header",
                        maximum=2_000,
                    )
                }
            )
            continue
        row = {
            "line": _projection_string(
                segment.get("line"),
                field="podcast transcript line",
                maximum=20_000,
            )
        }
        if segment.get("sec") is not None:
            row["sec"] = segment["sec"]
        if segment.get("time"):
            row["time"] = _projection_string(
                segment["time"], field="podcast transcript time", maximum=50
            )
        if segment.get("who"):
            row["who"] = _projection_string(
                segment["who"], field="podcast transcript speaker", maximum=1_000
            )
        result.append(row)
    return result


def published_catalogue() -> dict[str, Any]:
    """The published records this parity check reads, in the shape it reads them.

    The check compares an accepted checkout against what the site actually
    publishes, so it assembles exactly the collections it looks at rather than
    asking for a whole catalogue in one value.
    """

    return {
        "manifest": catalogue.manifest(),
        "articles": catalogue.articles(),
        "podcasts": catalogue.podcasts(),
        "books": catalogue.books(),
        "media": catalogue.media(),
        "people_by_slug": catalogue.people_by_slug(),
    }


def verify_initial_projection_parity(
    bundle: CandidateBundle,
    *,
    projection: Mapping[str, Any] | None = None,
    projection_root: Path = DEFAULT_PROJECTION_ROOT,
) -> ProjectionParityEvidence:
    """Prove the accepted adapter bundle equals #105's adopted checked projection."""

    if bundle.commit_sha != ACCEPTED_CONTENT_COMMIT:
        _fail("projection_parity_not_initial_commit")
    if (
        bundle.source_tree_sha != ACCEPTED_CONTENT_TREE
        or bundle.repaired_baseline_commit != REPAIRED_BASELINE_COMMIT
        or bundle.repaired_baseline_tree != REPAIRED_BASELINE_TREE
        or bundle.repaired_baseline_ci_run != REPAIRED_BASELINE_CI_RUN
        or bundle.repair_manifest_sha256 != REPAIR_MANIFEST_SHA256
        or bundle.replacement_attestation_sha256 != REPLACEMENT_ATTESTATION_SHA256
        or bundle.repair_completion_reference != REPAIR_COMPLETION_REFERENCE
        or bundle.editorial_overlay_sha256 != EDITORIAL_OVERLAY_SHA256
        or bundle.source_ci_run != SOURCE_CI_RUN
        or dict(bundle.counts) != ACCEPTED_COUNTS
    ):
        _fail("projection_source_evidence_mismatch")
    checked = projection or published_catalogue()
    manifest = checked.get("manifest")
    if not isinstance(manifest, dict):
        _fail("projection_manifest_mismatch")
    sources = manifest.get("sources")
    counts = manifest.get("counts")
    if not isinstance(sources, dict) or not isinstance(counts, dict):
        _fail("projection_manifest_mismatch")
    preferred = sources.get("preferred_content", {})
    if (
        manifest.get("selection_mode") != "preferred"
        or manifest.get("tree_sha256") != PROJECTION_TREE_SHA256
        or counts.get("media") != 1_253
        or preferred
        != {
            "accepted": True,
            "ci_run": SOURCE_CI_RUN,
            "editorial_overlay_sha256": EDITORIAL_OVERLAY_SHA256,
            "repair_manifest_sha256": REPAIR_MANIFEST_SHA256,
            "repository": "DataTalksClub/content",
            "revision": ACCEPTED_CONTENT_COMMIT,
            "tree": ACCEPTED_CONTENT_TREE,
        }
    ):
        _fail("projection_manifest_mismatch")
    manifest_path = projection_root / "manifest.json"
    try:
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError:
        _fail("projection_manifest_unreadable")
    if manifest_sha256 != PROJECTION_MANIFEST_SHA256:
        _fail("projection_manifest_digest_mismatch")
    podcasts_path = projection_root / "podcasts.json"
    try:
        podcasts_sha256 = hashlib.sha256(podcasts_path.read_bytes()).hexdigest()
    except OSError:
        _fail("projection_podcasts_unreadable")
    if podcasts_sha256 != PROJECTION_PODCASTS_SHA256:
        _fail("projection_podcasts_digest_mismatch")

    documents = {(item.content_kind, item.stable_key): item for item in bundle.documents}
    if len(documents) != len(bundle.documents) or len(documents) != 561:
        _fail("projection_document_count_mismatch")
    relations = bundle.relations
    approved_person_keys = frozenset(str(key) for key in checked["people_by_slug"])
    optional_person_relations = 0
    for relation in relations:
        if relation.target_kind != "person":
            continue
        expected_required = relation.target_key in approved_person_keys
        _expect(
            relation.is_required,
            expected_required,
            code="projection_relation_resolution_mismatch",
            source_path=".",
        )
        if not relation.is_required:
            optional_person_relations += 1
    contracts = {
        contract.percent_encoded_public_reference: contract
        for contract in content_route_contracts()
    }
    observed_contracts = 0
    unobserved_contracts = 0
    source_entries: list[dict[str, str]] = []
    semantic_entries: list[dict[str, Any]] = []

    for kind, collection in (("article", "articles"), ("podcast", "podcasts"), ("book", "books")):
        records = checked.get(collection)
        expected_count = ACCEPTED_COUNTS[collection]
        if not isinstance(records, (list, tuple)) or len(records) != expected_count:
            _fail("projection_collection_count_mismatch", collection)
        for record in records:
            if not isinstance(record, dict):
                _fail("projection_record_invalid", collection)
            key = str(record.get("slug") or "")
            document = documents.get((kind, key))
            if document is None:
                _fail(
                    "projection_document_missing",
                    str(record.get("provenance", {}).get("source_path") or collection),
                )
            expected_provenance = _provenance(
                document,
                repository="DataTalksClub/content",
                revision=ACCEPTED_CONTENT_COMMIT,
            )
            _expect(
                record.get("provenance"),
                expected_provenance,
                code="projection_provenance_mismatch",
                source_path=document.source_path,
            )
            source_entries.append({"kind": kind, **expected_provenance})
            if _expect_contract_identity(
                document,
                public_path=str(record["public_path"]),
                contracts=contracts,
                expect_asset=False,
                source_path=document.source_path,
            ):
                observed_contracts += 1
            else:
                unobserved_contracts += 1

            if kind == "article":
                metadata = document.raw_frontmatter
                if not isinstance(metadata, Mapping):
                    _fail("projection_article_metadata_mismatch", document.source_path)
                subtitle = _projection_string(
                    metadata.get("subtitle"),
                    field="article subtitle",
                    maximum=2_000,
                    optional=True,
                )
                description = _projection_string(
                    metadata.get("description"),
                    field="article description",
                    maximum=4_000,
                    optional=True,
                )
                image_source = _projection_string(
                    metadata.get("image"),
                    field="article image",
                    maximum=500,
                    optional=True,
                )
                image_path = f"/{image_source.lstrip('/')}"
                _common_document(
                    document,
                    record,
                    seo_title=f"{record['title']} — DataTalks.Club",
                    summary=subtitle or description,
                    seo_description=description,
                    image_path=image_path,
                )
                _expect(
                    record.get("subtitle"),
                    subtitle,
                    code="projection_article_metadata_mismatch",
                    source_path=document.source_path,
                )
                _expect(
                    record.get("description"),
                    description,
                    code="projection_article_metadata_mismatch",
                    source_path=document.source_path,
                )
                _expect(
                    record.get("image_path"),
                    image_path,
                    code="projection_article_media_mismatch",
                    source_path=document.source_path,
                )
                _expect(
                    _comparable_article_blocks(record.get("blocks")),
                    _comparable_article_blocks(
                        _article_blocks(document.raw_body, media_root=None, counters={})
                    ),
                    code="projection_article_body_mismatch",
                    source_path=document.source_path,
                )
                author_keys = _relation_keys(
                    relations, source_kind=kind, source_key=key, relation_type="author"
                )
                _expect(
                    record.get("authors"),
                    author_keys,
                    code="projection_relation_mismatch",
                    source_path=document.source_path,
                )
                _published_date(document, record)
            elif kind == "podcast":
                metadata = _structured(document)
                description = _projection_string(
                    metadata.get("description"),
                    field="podcast description",
                    maximum=20_000,
                )
                season = _positive_integer(
                    metadata.get("season"),
                    code="projection_podcast_season_invalid",
                    source_path=document.source_path,
                )
                episode = _positive_integer(
                    metadata.get("episode"),
                    code="projection_podcast_episode_invalid",
                    source_path=document.source_path,
                )
                projection_season = _positive_integer(
                    record.get("season"),
                    code="projection_podcast_season_invalid",
                    source_path=document.source_path,
                )
                projection_episode = _positive_integer(
                    record.get("episode"),
                    code="projection_podcast_episode_invalid",
                    source_path=document.source_path,
                )
                image_source = _projection_string(
                    metadata.get("image"),
                    field="podcast image",
                    maximum=500,
                    optional=True,
                )
                image_path = f"/{image_source.lstrip('/')}"
                _common_document(
                    document,
                    record,
                    seo_title=f"{record['title']} — DataTalks.Club Podcast",
                    summary=description,
                    seo_description=description,
                    image_path=image_path,
                )
                expected_links: dict[str, str] = {}
                for label, value in sorted((metadata.get("links") or {}).items()):
                    if value == "TODO":
                        continue
                    provider = _canonical_podcast_platform_key(label)
                    expected_links[provider] = _canonical_podcast_platform_url(provider, value)
                expected_resources = [
                    resource.as_dict()
                    for resource in normalize_podcast_resources(
                        metadata.get("resources"),
                        records=records,
                        strict=False,
                    )
                ]
                actual_resources = [
                    resource.as_dict()
                    for resource in normalize_podcast_resources(
                        record.get("resources"),
                        records=records,
                        strict=False,
                    )
                ]
                podcast_expectations: tuple[tuple[str, Any], ...] = (
                    (
                        "short",
                        _projection_string(
                            metadata.get("short"),
                            field="podcast short",
                            maximum=500,
                            optional=True,
                        ),
                    ),
                    ("description", description),
                    ("season", season),
                    ("episode", episode),
                    (
                        "guests",
                        _relation_keys(
                            relations, source_kind=kind, source_key=key, relation_type="guest"
                        ),
                    ),
                    ("links", expected_links),
                    ("resources", expected_resources),
                    ("image_path", image_path),
                )
                for field, expected in podcast_expectations:
                    actual = record.get(field)
                    if field == "season":
                        actual = projection_season
                    elif field == "episode":
                        actual = projection_episode
                    elif field == "resources":
                        actual = actual_resources
                    _expect(
                        actual,
                        expected,
                        code="projection_podcast_metadata_mismatch",
                        source_path=document.source_path,
                    )
                transcript = documents.get(("podcast_transcript", key))
                transcript_relations = _relation_keys(
                    relations, source_kind=kind, source_key=key, relation_type="transcript"
                )
                if transcript is None:
                    _expect(
                        record.get("transcript"),
                        [],
                        code="projection_transcript_mismatch",
                        source_path=document.source_path,
                    )
                    _expect(
                        transcript_relations,
                        [],
                        code="projection_transcript_mismatch",
                        source_path=document.source_path,
                    )
                else:
                    transcript_metadata = _structured(transcript)
                    _expect(
                        transcript_relations,
                        [key],
                        code="projection_transcript_mismatch",
                        source_path=document.source_path,
                    )
                    _expect(
                        record.get("transcript"),
                        _transcript_segments(
                            transcript_metadata.get("segments"), source_path=transcript.source_path
                        ),
                        code="projection_transcript_mismatch",
                        source_path=transcript.source_path,
                    )
                    _expect(
                        transcript.exact_public_path,
                        None,
                        code="projection_transcript_route_mismatch",
                        source_path=transcript.source_path,
                    )
                    _expect(
                        transcript.is_published,
                        False,
                        code="projection_transcript_publish_mismatch",
                        source_path=transcript.source_path,
                    )
                    _expect(
                        transcript.noindex,
                        True,
                        code="projection_transcript_noindex_mismatch",
                        source_path=transcript.source_path,
                    )
                    transcript_provenance = _provenance(
                        transcript,
                        repository="DataTalksClub/content",
                        revision=ACCEPTED_CONTENT_COMMIT,
                    )
                    _expect(
                        record.get("transcript_provenance"),
                        transcript_provenance,
                        code="projection_transcript_provenance_mismatch",
                        source_path=transcript.source_path,
                    )
                    source_entries.append({"kind": "podcast_transcript", **transcript_provenance})
                _published_date(document, record)
            else:
                metadata = _structured(document)
                description = _projection_string(
                    metadata.get("description"),
                    field="book description",
                    maximum=4_000,
                    optional=True,
                )
                image_source = _projection_string(
                    metadata.get("image") or metadata.get("cover"),
                    field="book image",
                    maximum=500,
                    optional=True,
                )
                image_path = f"/{image_source.lstrip('/')}"
                _common_document(
                    document,
                    record,
                    seo_title=f"{record['title']} — DataTalks.Club Books",
                    summary=description,
                    seo_description=description,
                    image_path=image_path,
                )
                links = [
                    {
                        "label": _projection_string(
                            item.get("text") or "Book link", field="book link label"
                        ),
                        "url": item.get("link") or item.get("list"),
                    }
                    for item in metadata.get("links", [])
                ]
                book_expectations: tuple[tuple[str, Any], ...] = (
                    ("description", description),
                    (
                        "summary",
                        _projection_string(
                            metadata.get("summary"),
                            field="book summary",
                            maximum=100_000,
                            optional=True,
                        ),
                    ),
                    (
                        "authors",
                        _relation_keys(
                            relations, source_kind=kind, source_key=key, relation_type="book_author"
                        ),
                    ),
                    ("links", links),
                    ("image_path", image_path),
                )
                for field, expected in book_expectations:
                    _expect(
                        record.get(field),
                        expected,
                        code="projection_book_metadata_mismatch",
                        source_path=document.source_path,
                    )
                _published_date(document, record)

            semantic_entries.append(
                {
                    "kind": kind,
                    "key": key,
                    "source_path": document.source_path,
                    "checksum": document.checksum,
                    "raw_frontmatter_sha256": _canonical_sha256(document.raw_frontmatter or {}),
                    "raw_body_sha256": hashlib.sha256(document.raw_body.encode()).hexdigest(),
                    "raw_structured_data_sha256": hashlib.sha256(
                        document.raw_structured_data.encode()
                    ).hexdigest(),
                    "contract_id": document.contract_id,
                }
            )

    projection_media = [
        item
        for item in checked.get("media", ())
        if item.get("provenance", {}).get("repository") == "DataTalksClub/content"
        and item.get("provenance", {}).get("revision") == ACCEPTED_CONTENT_COMMIT
    ]
    if (
        len(projection_media) != ACCEPTED_COUNTS["media"]
        or len(bundle.assets) != ACCEPTED_COUNTS["media"]
    ):
        _fail("projection_media_count_mismatch")
    assets = {asset.source_path: asset for asset in bundle.assets}
    for record in projection_media:
        source_path = str(record.get("provenance", {}).get("source_path") or "")
        asset = assets.get(source_path)
        if asset is None:
            _fail("projection_media_missing", source_path)
        expected_provenance = {
            "repository": "DataTalksClub/content",
            "revision": ACCEPTED_CONTENT_COMMIT,
            "source_path": source_path,
            "source_key": source_path,
            "checksum": asset.checksum,
            "source_url": (
                "https://github.com/DataTalksClub/content/blob/"
                f"{ACCEPTED_CONTENT_COMMIT}/{quote(source_path, safe='/')}"
            ),
        }
        _expect(
            record.get("provenance"),
            expected_provenance,
            code="projection_media_provenance_mismatch",
            source_path=source_path,
        )
        _expect(
            record.get("slug"),
            source_path,
            code="projection_media_key_mismatch",
            source_path=source_path,
        )
        _expect(
            record.get("public_path"),
            f"/{source_path}",
            code="projection_media_path_mismatch",
            source_path=source_path,
        )
        _expect(
            asset.stable_public_path,
            f"/{quote(source_path, safe='/')}",
            code="projection_media_path_mismatch",
            source_path=source_path,
        )
        _expect(
            record.get("content_type"),
            asset.content_type,
            code="projection_media_type_mismatch",
            source_path=source_path,
        )
        if _expect_contract_identity(
            asset,
            public_path=asset.stable_public_path,
            contracts=contracts,
            expect_asset=True,
            source_path=source_path,
        ):
            observed_contracts += 1
        else:
            unobserved_contracts += 1
        try:
            copied_path = (
                projection_root / "media" / PurePosixPath(source_path).relative_to("images")
            )
            copied_size = copied_path.stat().st_size
        except (OSError, ValueError):
            _fail("projection_media_unreadable", source_path)
        _expect(
            copied_size, asset.size, code="projection_media_size_mismatch", source_path=source_path
        )
        source_entries.append({"kind": "media", **expected_provenance})
        semantic_entries.append(
            {
                "kind": "media",
                "key": source_path,
                "checksum": asset.checksum,
                "content_type": asset.content_type,
                "size": asset.size,
                "public_path": asset.stable_public_path,
                "contract_id": asset.contract_id,
            }
        )

    source_entries.sort(key=lambda item: (item["kind"], item["source_path"]))
    semantic_entries.sort(key=lambda item: (item["kind"], item["key"]))
    adopted_source_set_sha256 = _canonical_sha256(source_entries)
    comparison_sha256 = _canonical_sha256(
        {
            "declared_transforms": _DECLARED_TRANSFORMS,
            "source": source_entries,
            "semantics": semantic_entries,
        }
    )
    if (
        adopted_source_set_sha256 != ACCEPTED_ADOPTED_SOURCE_SET_SHA256
        or comparison_sha256 != ACCEPTED_COMPARISON_SHA256
    ):
        _fail("projection_comparison_digest_mismatch")
    return ProjectionParityEvidence(
        status="PASS",
        projection_manifest_sha256=manifest_sha256,
        projection_tree_sha256=PROJECTION_TREE_SHA256,
        projection_podcasts_sha256=podcasts_sha256,
        adopted_source_set_sha256=adopted_source_set_sha256,
        comparison_sha256=comparison_sha256,
        counts={
            "articles": ACCEPTED_COUNTS["articles"],
            "podcasts": ACCEPTED_COUNTS["podcasts"],
            "podcast_transcripts": ACCEPTED_COUNTS["podcast_transcripts"],
            "books": ACCEPTED_COUNTS["books"],
            "media": ACCEPTED_COUNTS["media"],
            "documents": 561,
            "projection_media_total": 1_253,
            "projection_person_media": 438,
            "observed_legacy_contracts": observed_contracts,
            "unobserved_legacy_contracts": unobserved_contracts,
            "optional_unresolved_person_relations": optional_person_relations,
        },
        declared_transforms=_DECLARED_TRANSFORMS,
    )
